import xml.etree.ElementTree as ET
from aimixmaster.clip_alignment import _has_fade


def test_zero_fades_container_is_not_an_active_fade():
    clip = ET.fromstring('<AudioClip><Fades><FadeInLength Value="0"/><FadeOutLength Value="0"/></Fades></AudioClip>')
    assert not _has_fade(clip)


def test_nonzero_fade_in_and_out_are_active():
    assert _has_fade(ET.fromstring('<AudioClip><Fades><FadeInLength Value="0.1"/></Fades></AudioClip>'))
    assert _has_fade(ET.fromstring('<AudioClip><Fades><FadeOutLength Value="0.1"/></Fades></AudioClip>'))
