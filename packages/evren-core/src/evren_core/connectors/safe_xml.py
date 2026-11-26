"""Güvenli XML ayrıştırma (U5.2, ADR-0020 §13): standart kütüphane expat, DTD ve varlık yok, ek bağımlılık yok.

Kaynak yanıtı (ör. NNMi SOAP) güvenilmeyen veridir. ``xml.etree`` doğrudan kullanılmaz; expat şu kurallarla çalıştırılır:

* ``<!DOCTYPE …>`` reddedilir: iç DTD ile varlık genişletme (billion laughs) ve dış DTD yolu kapanır.
* Varlık tanımı ve dış varlık başvurusu reddedilir (XXE yok); yalnızca XML'in yerleşik beş varlığı çözülür.
* İşleme talimatları yok sayılır; boyut üst sınırı HTTP katmanındadır (64 MB), derinlik burada sınırlıdır.

Sonuç sade bir ağaçtır (``Node``): yerel ad (ad alanı öneki atılır), öznitelikler, çocuklar, metin.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# DTD, varlık tanımı ve dış başvuru aşağıdaki işleyicilerle reddedilir (bandit B407 bu yüzden bilinçli olarak geçilir)
from xml.parsers import expat  # nosec B407

__all__ = ["MAX_DEPTH", "Node", "UnsafeXml", "parse"]

MAX_DEPTH = 64


class UnsafeXml(ValueError):
    """XML güvenlik kuralını çiğniyor veya bozuk; içerik iletiye yazılmaz."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    text: str = ""

    def find(self, tag: str) -> Node | None:
        return next((c for c in self.children if c.tag == tag), None)

    def findall(self, tag: str) -> list[Node]:
        return [c for c in self.children if c.tag == tag]

    def iter(self, tag: str) -> list[Node]:
        out = [self] if self.tag == tag else []
        for c in self.children:
            out.extend(c.iter(tag))
        return out


def _local(name: str) -> str:
    return name.rsplit(" ", 1)[-1].rsplit(":", 1)[-1]


def parse(data: bytes) -> Node:
    parser = expat.ParserCreate(namespace_separator=" ")
    stack: list[Node] = []
    root: list[Node] = []

    def refuse(*_args: object) -> None:
        raise UnsafeXml("XML'de DTD veya varlık tanımına izin verilmez")

    def start(name: str, attrs: dict[str, str]) -> None:
        if len(stack) >= MAX_DEPTH:
            raise UnsafeXml("XML çok derin")
        node = Node(_local(name), {_local(k): v for k, v in attrs.items()})
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_name: str) -> None:
        node = stack.pop()
        node.text = node.text.strip()

    def chars(text: str) -> None:
        if stack:
            stack[-1].text += text

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        parser.Parse(data, True)
    except expat.ExpatError:
        raise UnsafeXml("XML bozuk") from None
    if len(root) != 1:
        raise UnsafeXml("XML kök öğesi yok")
    return root[0]
