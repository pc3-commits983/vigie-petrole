"""Diagnostic de couverture du flux aisstream, région par région.

    python -m pipeline.diagnostic            (clé dans AISSTREAM_API_KEY)

Pour chaque région, on s'abonne à cette seule zone pendant quelques dizaines de secondes et on
compte les messages, les navires distincts et les navires-citernes identifiés. Le résultat est
écrit en tableau Markdown (et dans le résumé GitHub Actions s'il existe).
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from . import config

URL = "wss://stream.aisstream.io/v0/stream"
REGIONS = [
    ("Monde", [[-90, -180], [90, 180]]),
    ("Golfe persique et Ormuz", [[23.0, 47.5], [30.6, 60.0]]),
    ("Mer Rouge", [[11.0, 32.3], [28.5, 45.0]]),
    ("Malacca et Singapour", [[-1.0, 95.0], [7.0, 105.0]]),
    ("Mer du Nord et Manche", [[48.5, -6.0], [62.0, 9.5]]),
    ("Golfe du Mexique", [[18.0, -98.0], [31.0, -80.5]]),
    ("Côtes chinoises", [[18.0, 108.0], [41.0, 126.0]]),
]


async def mesurer(cle: str, boite, secondes: float) -> dict:
    import websockets

    messages, positions, statiques, citernes = 0, set(), set(), set()
    largeurs, accuse = [], None
    async with websockets.connect(URL, compression="deflate", open_timeout=30, max_size=2**23) as ws:
        await ws.send(json.dumps({"APIKey": cle, "BoundingBoxes": [boite], "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}))
        fin = time.monotonic() + secondes
        while (reste := fin - time.monotonic()) > 0:
            try:
                brut = await asyncio.wait_for(ws.recv(), timeout=reste)
            except asyncio.TimeoutError:
                break
            m = json.loads(brut)
            if "MessageType" not in m:
                accuse = accuse or {k: v for k, v in m.items() if "key" not in k.lower()}
                continue
            messages += 1
            corps = m.get("Message", {}).get(m["MessageType"], {})
            mmsi = corps.get("UserID")
            if m["MessageType"] == "PositionReport":
                positions.add(mmsi)
            else:
                statiques.add(mmsi)
                if corps.get("Type") in config.TYPES_CITERNE:
                    citernes.add(mmsi)
                    d = corps.get("Dimension") or {}
                    largeurs.append((d.get("C") or 0) + (d.get("D") or 0))
        extensions = getattr(ws, "extensions", None) or getattr(getattr(ws, "protocol", None), "extensions", [])
    etroites = sum(1 for l in largeurs if 0 < l < 14)
    return {
        "messages_par_s": round(messages / secondes, 1),
        "navires_positions": len(positions),
        "navires_statiques": len(statiques),
        "citernes": len(citernes),
        "citernes_largeur_moins_14m": etroites,
        "compression_negociee": [type(e).__name__ for e in extensions],
        "accuse": accuse,
    }


def main() -> None:
    cle = os.environ["AISSTREAM_API_KEY"].strip()
    secondes = float(os.environ.get("DIAGNOSTIC_SECONDES", "60"))
    lignes = ["| Région | msg/s | navires (positions) | navires (statiques) | citernes | dont largeur < 14 m |", "|---|---|---|---|---|---|"]
    accuses = []
    for nom, boite in REGIONS:
        try:
            r = asyncio.run(mesurer(cle, boite, secondes))
            lignes.append(f"| {nom} | {r['messages_par_s']} | {r['navires_positions']} | {r['navires_statiques']} | {r['citernes']} | {r['citernes_largeur_moins_14m']} |")
            accuses.append(f"- {nom} : extensions {r['compression_negociee']}, accusé {json.dumps(r['accuse'], ensure_ascii=False)}")
        except Exception as exc:
            lignes.append(f"| {nom} | erreur : {type(exc).__name__} {exc} | | | | |")
        time.sleep(2)
    texte = f"## Couverture aisstream ({secondes:.0f} s par région)\n\n" + "\n".join(lignes) + "\n\n" + "\n".join(accuses) + "\n"
    print(texte)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(texte)


if __name__ == "__main__":
    main()
