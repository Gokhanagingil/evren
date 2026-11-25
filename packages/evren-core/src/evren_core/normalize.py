"""Kaynak sistemlerden gelen tanımlayıcıların normalizasyonu.

Bu modül *eşleştirme anahtarı* üretir; golden record'da gösterilecek değeri
değiştirmez. Golden record'a yazılan değer, uzlaştırma kurallarına göre
kazanan kaynağın (temizlenmiş) orijinal değeridir.

Tasarım ilkeleri
----------------
* Hiçbir fonksiyon yerel ayara (locale) bağlı değildir. Türkçe ``İ/ı``
  dönüşümleri açıkça ele alınır; ``str.lower()`` tek başına güvenilmez
  (Python'da ``"İ".lower()`` iki karakter döndürür).
* "Çöp" değerler (``To be filled by O.E.M.``, sıfır UUID, HSRP sanal MAC vb.)
  kimlik olarak kullanılmaz; bunlar yanlış birleştirmenin bir numaralı
  nedenidir.
* Her fonksiyon geçersiz girdide ``None`` döndürür, istisna fırlatmaz.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "HostName",
    "Ip",
    "Mac",
    "Serial",
    "ascii_fold",
    "normalize_hostname",
    "normalize_ip",
    "normalize_mac",
    "normalize_serial",
    "normalize_uuid",
    "os_family",
    "uuid_byte_swapped",
    "uuid_from_vmware_serial",
    "uuid_variants",
]

# ---------------------------------------------------------------------------
# Genel yardımcılar
# ---------------------------------------------------------------------------

_TR_FOLD = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
    }
)


def ascii_fold(value: str) -> str:
    """Türkçe ve aksanlı karakterleri ASCII karşılığına indirger, küçük harfe çevirir."""
    folded = value.translate(_TR_FOLD)
    decomposed = unicodedata.normalize("NFKD", folded)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.encode("ascii", "ignore").decode("ascii").lower()


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("'\"").strip()
    return text or None


# ---------------------------------------------------------------------------
# Hostname
# ---------------------------------------------------------------------------

#: Kimlik olarak kullanılmayacak ad değerleri (kanonik biçimde).
JUNK_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost-localdomain",
        "unknown",
        "none",
        "null",
        "n-a",
        "na",
        "default",
        "undefined",
        "computer",
        "host",
    }
)

_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_SEPARATORS = re.compile(r"[\s_]+")
_NOT_LABEL_CHARS = re.compile(r"[^a-z0-9-]")
_MULTI_DASH = re.compile(r"-{2,}")


@dataclass(frozen=True)
class HostName:
    """Bir hostname'in eşleştirme için ayrıştırılmış biçimi.

    ``short``   : ilk etiket, ayraçlar ``-`` olarak birleştirilmiş (``abc_1`` → ``abc-1``)
    ``domain``  : alan adı kısmı (``corp.local``) veya ``@local`` gibi realm eki
    ``fqdn``    : ``short`` + ``domain``; alan adı yoksa ``None``
    ``compact`` : ayraçsız biçim (``abc1``); yalnızca aday bulmak (blocking) içindir,
                  tek başına otomatik birleştirme gerekçesi değildir.
    """

    raw: str
    short: str | None
    domain: str | None
    fqdn: str | None
    compact: str | None
    is_ip: bool = False
    is_junk: bool = False

    @property
    def usable(self) -> bool:
        return bool(self.short) and not self.is_ip and not self.is_junk


def _canonical_label(label: str) -> str:
    label = _SEPARATORS.sub("-", label)
    label = _NOT_LABEL_CHARS.sub("", label)
    label = _MULTI_DASH.sub("-", label)
    return label.strip("-")


def normalize_hostname(raw: object, *, strip_parenthetical: bool = True) -> HostName | None:
    """Farklı kaynaklardaki ad biçimlerini aynı anahtara indirger.

    >>> normalize_hostname("abc_1@local").short
    'abc-1'
    >>> normalize_hostname("ABC-1.corp.local.").fqdn
    'abc-1.corp.local'
    >>> normalize_hostname("CORP\\\\ABC_1").short
    'abc-1'
    """
    text = _clean(raw)
    if text is None:
        return None
    original = text

    # NetBIOS önekini at: CORP\host
    if "\\" in text:
        text = text.rsplit("\\", 1)[-1]

    # Realm/alan eki: host@local, host@corp.local
    at_domain: str | None = None
    if "@" in text:
        text, _, at_domain = text.partition("@")

    if strip_parenthetical:
        text = _TRAILING_PAREN.sub("", text)

    text = ascii_fold(text).strip().rstrip(".")
    if not text:
        return HostName(original, None, None, None, None, is_junk=True)

    # IP ile adlandırılmış kayıt (ör. Zabbix'te host adı olarak IP)
    try:
        ipaddress.ip_address(text)
        return HostName(original, None, None, None, None, is_ip=True)
    except ValueError:
        pass

    first, _, rest = text.partition(".")
    short = _canonical_label(first)
    domain_raw = rest or (ascii_fold(at_domain).strip(".") if at_domain else "")
    domain_labels = [_canonical_label(p) for p in domain_raw.split(".") if p]
    domain = ".".join(p for p in domain_labels if p) or None

    if not short:
        return HostName(original, None, domain, None, None, is_junk=True)

    junk = short in JUNK_HOSTNAMES or f"{short}-{domain}" in JUNK_HOSTNAMES
    fqdn = f"{short}.{domain}" if domain else None
    compact = short.replace("-", "")
    return HostName(original, short, domain, fqdn, compact, is_junk=junk)


# ---------------------------------------------------------------------------
# Seri numarası
# ---------------------------------------------------------------------------

JUNK_SERIALS = frozenset(
    {
        "0",
        "NONE",
        "N/A",
        "NA",
        "NULL",
        "UNKNOWN",
        "INVALID",
        "DEFAULT",
        "DEFAULTSTRING",
        "NOTSPECIFIED",
        "NOTAPPLICABLE",
        "NOTAVAILABLE",
        "TOBEFILLEDBYO.E.M.",
        "TOBEFILLEDBYOEM",
        "SYSTEMSERIALNUMBER",
        "CHASSISSERIALNUMBER",
        "SERIALNUMBER",
        "0123456789",
        "123456789",
        "1234567890",
        "OEM",
    }
)

_JUNK_SERIAL_FRAGMENTS = ("FILLEDBY", "O.E.M", "SERIALNUMBER", "NOTSPECIFIED")
_WHITESPACE = re.compile(r"\s+")
_VMWARE_SERIAL = re.compile(r"^VMWARE-([0-9A-F]{16})-?([0-9A-F]{16})$")


@dataclass(frozen=True)
class Serial:
    """Normalleştirilmiş seri numarası.

    ``kind``: ``hardware`` (fiziksel cihaz) veya ``vmware`` (BIOS UUID'den türeyen
    sanal makine seri numarası). VMware seri numarası, VM'in BIOS UUID'sini taşır
    ve VM ↔ misafir sunucu eşleştirmesinde güçlü bir kanıttır.
    """

    value: str
    kind: str


def normalize_serial(raw: object) -> Serial | None:
    text = _clean(raw)
    if text is None:
        return None
    compact = _WHITESPACE.sub("", text).upper()
    if len(compact) < 4 or compact in JUNK_SERIALS:
        return None
    if any(fragment in compact for fragment in _JUNK_SERIAL_FRAGMENTS):
        return None
    if re.fullmatch(r"(.)\1+", compact):  # 00000000, XXXXXXXX
        return None
    if _VMWARE_SERIAL.match(compact):
        return Serial(compact, "vmware")
    return Serial(compact, "hardware")


# ---------------------------------------------------------------------------
# UUID (BIOS / SMBIOS / örnek kimlikleri)
# ---------------------------------------------------------------------------

#: Birçok anakartın fabrika çıkışı aynı gönderdiği, kimlik olamayacak UUID'ler.
JUNK_UUIDS = frozenset(
    {
        "00000000000000000000000000000000",
        "ffffffffffffffffffffffffffffffff",
        "03000200040005000006000700080009",
        "12345678123456781234567812345678",
    }
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _hyphenate(h: str) -> str:
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def normalize_uuid(raw: object) -> str | None:
    text = _clean(raw)
    if text is None:
        return None
    h = text.strip("{}").replace("-", "").replace(" ", "").lower()
    if not _HEX32.match(h) or h in JUNK_UUIDS:
        return None
    return _hyphenate(h)


def uuid_byte_swapped(value: str) -> str:
    """İlk üç alanı bayt sırası ters çevrilmiş UUID'yi döndürür.

    SMBIOS 2.6 öncesi araçlar ve bazı ajanlar UUID'nin ilk üç alanını
    little-endian okur; vCenter ile işletim sisteminin bildirdiği UUID bu yüzden
    farklı görünebilir. Eşleştirmede iki biçim de denenir.
    """
    h = value.replace("-", "")

    def swap(part: str) -> str:
        return "".join(reversed([part[i : i + 2] for i in range(0, len(part), 2)]))

    return _hyphenate(swap(h[0:8]) + swap(h[8:12]) + swap(h[12:16]) + h[16:32])


def uuid_variants(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset({value, uuid_byte_swapped(value)})


def uuid_from_vmware_serial(serial: Serial | str | None) -> str | None:
    """``VMware-42 1a ... -81 92 ...`` seri numarasından BIOS UUID'yi türetir."""
    if serial is None:
        return None
    value = serial.value if isinstance(serial, Serial) else _WHITESPACE.sub("", str(serial)).upper()
    match = _VMWARE_SERIAL.match(value)
    if not match:
        return None
    return normalize_uuid(match.group(1) + match.group(2))


# ---------------------------------------------------------------------------
# MAC adresi
# ---------------------------------------------------------------------------

#: Birden fazla cihazın paylaştığı veya sanal/geçici adaptörlere ait önekler.
#: Bu MAC'ler kimlik kanıtı sayılmaz (zayıf).
WEAK_MAC_PREFIXES: tuple[tuple[str, str], ...] = (
    ("00:00:0c:07:ac", "HSRP v1 sanal MAC (yönlendiriciler arasında paylaşılır)"),
    ("00:00:0c:9f:f", "HSRP v2 sanal MAC (yönlendiriciler arasında paylaşılır)"),
    ("00:00:5e:00:01", "VRRP IPv4 sanal MAC (paylaşılır)"),
    ("00:00:5e:00:02", "VRRP IPv6 sanal MAC (paylaşılır)"),
    ("00:05:9a:3c:7a", "Cisco AnyConnect sanal adaptörü"),
    ("00:ff:", "VPN TAP sanal adaptörü"),
)

_HEX12 = re.compile(r"^[0-9a-f]{12}$")


@dataclass(frozen=True)
class Mac:
    value: str
    strong: bool
    reason: str | None = None


def normalize_mac(raw: object) -> Mac | None:
    """``00-50-56-AB-CD-EF``, ``0050.56ab.cdef``, ``005056abcdef`` → ``00:50:56:ab:cd:ef``."""
    text = _clean(raw)
    if text is None:
        return None
    h = re.sub(r"[:\-.\s]", "", text).lower()
    if not _HEX12.match(h) or h in ("000000000000", "ffffffffffff"):
        return None
    first_octet = int(h[0:2], 16)
    if first_octet & 0x01:  # multicast/broadcast: arayüz kimliği değildir
        return None
    value = ":".join(h[i : i + 2] for i in range(0, 12, 2))
    for prefix, reason in WEAK_MAC_PREFIXES:
        if value.startswith(prefix):
            return Mac(value, strong=False, reason=reason)
    if first_octet & 0x02:
        return Mac(value, strong=False, reason="Yerel yönetimli MAC (sanal/konteyner adaptörü)")
    return Mac(value, strong=True)


# ---------------------------------------------------------------------------
# IP adresi
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ip:
    value: str
    version: int
    private: bool


def normalize_ip(raw: object) -> Ip | None:
    """IP adresini doğrular. Loopback, link-local, multicast ve belirsiz adresler reddedilir.

    IP adresi zayıf bir kimlik kanıtıdır (DHCP, NAT, yeniden kullanım); tek başına
    birleştirme gerekçesi olmaz.
    """
    text = _clean(raw)
    if text is None:
        return None
    text = text.split("%", 1)[0].split("/", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return None
    return Ip(str(ip), ip.version, ip.is_private)


# ---------------------------------------------------------------------------
# İşletim sistemi ailesi
# ---------------------------------------------------------------------------

_OS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("esxi", re.compile(r"\besxi?\b|vmware esx", re.I)),
    ("windows", re.compile(r"windows|win(?:dows)?\s?(?:server|20\d\d)|microsoft", re.I)),
    ("aix", re.compile(r"\baix\b", re.I)),
    ("solaris", re.compile(r"solaris|sunos", re.I)),
    ("hpux", re.compile(r"hp-?ux", re.I)),
    ("network-os", re.compile(r"\bios(?:[- ]x[er])?\b|nx-?os|junos|fortios|pan-?os|arubaos|comware|\beos\b", re.I)),
    ("macos", re.compile(r"\bmac\s?os\b|darwin|\bos x\b", re.I)),
    (
        "linux",
        re.compile(
            r"linux|red ?hat|rhel|centos|ubuntu|debian|suse|sles|oracle linux|rocky|alma|amazon linux|fedora",
            re.I,
        ),
    ),
)


def os_family(raw: object) -> str | None:
    """İşletim sistemi metninden aileyi çıkarır (eşleştirmede çelişki kontrolü için)."""
    text = _clean(raw)
    if text is None:
        return None
    for family, pattern in _OS_PATTERNS:
        if pattern.search(text):
            return family
    return None
