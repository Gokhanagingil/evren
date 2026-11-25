import pytest

from evren_core.normalize import (
    ascii_fold,
    normalize_hostname,
    normalize_ip,
    normalize_mac,
    normalize_serial,
    normalize_uuid,
    os_family,
    uuid_byte_swapped,
    uuid_from_vmware_serial,
)


@pytest.mark.parametrize(
    "raw",
    ["abc_1", "abc-1", "abc_1@local", "ABC_1", "ABC-1.corp.local.", "CORP\\abc_1", " abc 1 ", "abc__1"],
)
def test_hostname_variants_share_canonical_short_name(raw):
    assert normalize_hostname(raw).short == "abc-1"


def test_hostname_compact_form_is_separator_free():
    assert normalize_hostname("abc-1").compact == "abc1"
    assert normalize_hostname("abc1").compact == "abc1"
    assert normalize_hostname("abc1").short != normalize_hostname("abc-1").short


def test_hostname_fqdn_and_domain():
    host = normalize_hostname("Web01.Corp.Sirket.com.tr")
    assert host.short == "web01"
    assert host.domain == "corp.sirket.com.tr"
    assert host.fqdn == "web01.corp.sirket.com.tr"


def test_turkish_characters_fold_without_locale_bugs():
    # Python'da "İ".lower() iki karakter üretir; normalizasyon bunu ASCII "i"ye indirmeli.
    assert ascii_fold("İSTANBUL-DB") == "istanbul-db"
    assert normalize_hostname("SUNUCU-İST01").short == "sunucu-ist01"
    assert normalize_hostname("ağ-çekirdek-01").short == "ag-cekirdek-01"


def test_ip_named_and_junk_hosts_are_not_identity():
    assert normalize_hostname("10.1.2.3").is_ip
    assert not normalize_hostname("10.1.2.3").usable
    assert normalize_hostname("localhost").is_junk
    assert normalize_hostname("localhost.localdomain").is_junk
    assert normalize_hostname("") is None
    assert normalize_hostname(None) is None


def test_trailing_parenthetical_vm_labels_are_removed():
    assert normalize_hostname("abc-1 (Prod DB)").short == "abc-1"


@pytest.mark.parametrize(
    "junk",
    ["To be filled by O.E.M.", "Default string", "0", "00000000", "XXXXXXXX", "System Serial Number", "N/A", "None", "abc"],
)
def test_junk_serials_rejected(junk):
    assert normalize_serial(junk) is None


def test_serial_normalized_and_vmware_kind_detected():
    assert normalize_serial(" cz1234abcd ").value == "CZ1234ABCD"
    s = normalize_serial("VMware-42 1a 2b 3c 4d 5e 6f 70-81 92 a3 b4 c5 d6 e7 f8")
    assert s.kind == "vmware"
    assert uuid_from_vmware_serial(s) == "421a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


def test_uuid_normalization_and_junk():
    assert normalize_uuid("{421A2B3C-4D5E-6F70-8192-A3B4C5D6E7F8}") == "421a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
    assert normalize_uuid("03000200-0400-0500-0006-000700080009") is None
    assert normalize_uuid("00000000-0000-0000-0000-000000000000") is None
    assert normalize_uuid("not-a-uuid") is None


def test_uuid_byte_swap_is_involution():
    u = "421a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
    assert uuid_byte_swapped(u) == "3c2b1a42-5e4d-706f-8192-a3b4c5d6e7f8"
    assert uuid_byte_swapped(uuid_byte_swapped(u)) == u


@pytest.mark.parametrize("raw", ["00-50-56-AB-CD-EF", "0050.56ab.cdef", "005056abcdef", "00:50:56:ab:cd:ef"])
def test_mac_formats(raw):
    mac = normalize_mac(raw)
    assert mac.value == "00:50:56:ab:cd:ef"
    assert mac.strong


def test_shared_and_virtual_macs_are_weak_or_rejected():
    assert not normalize_mac("00:00:0c:07:ac:0a").strong  # HSRP
    assert not normalize_mac("00:00:5e:00:01:01").strong  # VRRP
    assert not normalize_mac("02:42:ac:11:00:02").strong  # yerel yönetimli (docker)
    assert normalize_mac("01:00:5e:00:00:01") is None      # multicast
    assert normalize_mac("ff:ff:ff:ff:ff:ff") is None
    assert normalize_mac("00:00:00:00:00:00") is None


def test_ip_validation():
    assert normalize_ip("10.0.0.5").private
    assert normalize_ip("10.0.0.5/24").value == "10.0.0.5"
    assert normalize_ip("fe80::1%eth0") is None
    assert normalize_ip("127.0.0.1") is None
    assert normalize_ip("169.254.10.10") is None
    assert normalize_ip("bogus") is None


@pytest.mark.parametrize(
    ("raw", "family"),
    [
        ("Microsoft Windows Server 2019 Datacenter", "windows"),
        ("Red Hat Enterprise Linux 8 (64-bit)", "linux"),
        ("VMware ESXi 8.0.2", "esxi"),
        ("AIX 7.2", "aix"),
        ("Cisco IOS XE Software", "network-os"),
        ("BIOS revision", None),
    ],
)
def test_os_family(raw, family):
    assert os_family(raw) == family
