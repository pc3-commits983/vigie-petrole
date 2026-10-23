"""Icône de Vigie pétrole : un fût ivoire mat sur fond noir, bouchon rouge.

    python outils/icone.py site/      écrit icon.svg, icon-maskable.svg et apple-touch-icon.svg

Les PNG (192, 512, masquable, iOS) se rendent ensuite depuis ces SVG avec n'importe quel
navigateur ou outil de conversion. Le fût a les proportions d'un vrai fût de 200 litres ;
les cerclages sont des bandes d'épaisseur constante qui suivent la courbure du cylindre.
"""

from __future__ import annotations

import sys
from pathlib import Path

IVOIRE, NOIR, ROUGE = "#ECE9E3", "#111111", "#FF3B30"
CX, RX, RY = 256, 92, 26          # centre, demi-largeur, demi-hauteur des ellipses
HAUT, BAS = 140, 372              # centre de l'ellipse du couvercle et du fond
G, D = CX - RX, CX + RX
C1, C2 = HAUT + (BAS - HAUT) / 3, HAUT + 2 * (BAS - HAUT) / 3


def bande(y: float, e: float) -> str:
    """Bande d'épaisseur verticale constante e centrée sur la courbe du cylindre à la hauteur y."""
    h = e / 2
    return f"M{G} {y - h}A{RX} {RY} 0 0 0 {D} {y - h}V{y + h}A{RX} {RY} 0 0 1 {G} {y + h}Z"


def corps() -> str:
    return f"M{G} {HAUT}V{BAS}A{RX} {RY} 0 0 0 {D} {BAS}V{HAUT}Z"


def icone(fond_plein: bool = False, echelle: float = 1.0) -> str:
    fond = f'<rect width="512" height="512" fill="{NOIR}"/>' if fond_plein else f'<rect width="512" height="512" rx="112" fill="{NOIR}"/>'
    t = f"translate(256 256) scale({echelle}) translate(-256 -256)"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
{fond}
<defs><clipPath id="f"><path d="{corps()}"/></clipPath></defs>
<g transform="{t}">
  <path d="{corps()}" fill="{IVOIRE}"/>
  <g fill="{NOIR}" clip-path="url(#f)"><path d="{bande(C1, 9)}"/><path d="{bande(C2, 9)}"/><path d="{bande(HAUT + 4, 8)}"/></g>
  <ellipse cx="{CX}" cy="{HAUT}" rx="{RX - 1}" ry="{RY - 1}" fill="{IVOIRE}"/>
  <ellipse cx="{CX + 40}" cy="{HAUT - 2}" rx="12" ry="5.5" fill="{ROUGE}"/>
</g>
</svg>
'''


def main(dossier: str = ".") -> None:
    sortie = Path(dossier)
    (sortie / "icon.svg").write_text(icone())                                   # onglet, icône « any » arrondie
    (sortie / "icon-maskable.svg").write_text(icone(fond_plein=True, echelle=0.92))  # Android : zone sûre de 80 %
    (sortie / "apple-touch-icon.svg").write_text(icone(fond_plein=True, echelle=1.05))  # iOS arrondit lui-même


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
