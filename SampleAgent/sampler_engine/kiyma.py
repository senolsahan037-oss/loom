"""Kiyma makinesi — SampleOffset envelope'u URETIR, kopyalamaz.

Kullanicinin 512 envelope'undan cikarilan dilbilgisi (olculdu 2026-09-03):

  * basamak fonksiyonu: her zaman noktasinda IKI nokta (eski deger, yeni deger)
  * adim araligi 0.1875 vurus baskin (11.264 araligin 10.240'i), yaninda
    0.5 ve 0.125
  * degerler bir "ev" degeri etrafinda duruyor (-1.23 / -1.48 / -1.66) ve
    oradan uclara sicrayip geri donuyor: -6.15 … +3.94
  * gozlenen egri 31 nokta, ~4.4 vurus yayiliyor

Ayni egriyi her plaga basmak teknik degil kopya olur — kullanicinin kendi
sozu. Burasi o yuzden dilbilgisini kullanip her cagride BASKA bir egri uretir;
tohum (seed) deftere yazilir, yani her deneme tekrar uretilebilir.
"""

import random

STEPS = (0.125, 0.1875, 0.25, 0.5)
HOME = (-1.23, -1.48, -1.66)
JUMPS = (-6.15, -6.0, -5.17, -4.8, -3.45, 0.25, 1.48, 2.22, 2.34, 3.69, 3.94)


def generate(seed=None, steps=None, count=None, span_beats=4.0, jump_chance=0.6):
    """Basamakli SampleOffset egrisi. (offset_vurus, deger) ciftleri dondurur.

    Egri BOLGENIN ICINE oturur: 0'dan baslar ve span_beats'i gecmez. Olculdu
    2026-09-03: egri loop basindan ONCE uretilince noktalarin 0/14'u bolge
    icinde kaliyordu — Live'da ne gorunuyor ne is goruyordu.
    """
    rng = random.Random(seed)
    step = steps if steps else rng.choice(STEPS)
    n = count if count else rng.choice((15, 21, 27, 31))
    home = rng.choice(HOME)
    begin = 0.0
    max_steps = max(2, int(span_beats / step))
    n = min(n, max_steps * 2)

    points = [(begin, 0.0), (begin, home)]
    current = home
    for index in range(1, n // 2):
        when = round(begin + index * step, 4)
        if when > span_beats:
            break
        if rng.random() < jump_chance:
            target = rng.choice(JUMPS)
        else:
            target = rng.choice(HOME)
        points.append((when, current))
        points.append((when, target))
        current = target
    return points, {"seed": seed, "step": step, "count": len(points),
                    "home": home, "span_beats": round(points[-1][0] - points[0][0], 3)}
