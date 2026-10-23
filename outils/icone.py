"""Icône Vigie pétrole : un fût de pétrole vu de trois quarts, flancs droits, deux cerclages."""

G, D, H, B, RY = 168, 344, 120, 392, 24   # gauche, droite, haut, bas du corps, demi-hauteur des ellipses
CX, RX = (G + D) / 2, (D - G) / 2


def corps():
    return f"M{G} {H}V{B}A{RX} {RY} 0 0 0 {D} {B}V{H}Z"


def cerclage(y):
    return f"M{G} {y}A{RX} {RY} 0 0 0 {D} {y}"


def svg(fond_plein=False, echelle=1.0, teinte="rouge", fond="#0c0c0c", rayon=112):
    if teinte == "rouge":
        c, couvercle, rebord, ombre = "#ff3b30", "#ff6b61", "#d92d23", "#0c0c0c"
    else:
        c, couvercle, rebord, ombre = "#efefec", "#ffffff", "#c9c9c4", "#0c0c0c"
    fond_svg = f'<rect width="512" height="512" fill="{fond}"/>' if fond_plein else f'<rect width="512" height="512" rx="{rayon}" fill="{fond}"/>'
    t = f"translate(256 256) scale({echelle}) translate(-256 -256)"
    y1, y2 = H + (B - H) * 0.37, H + (B - H) * 0.70
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
{fond_svg}
<defs><clipPath id="f"><path d="{corps()}"/></clipPath></defs>
<g transform="{t}">
  <path d="{corps()}" fill="{c}"/>
  <rect x="{G + 22}" y="{H + 26}" width="14" height="{B - H - 30}" rx="7" fill="#fff" opacity="{0.2 if teinte == 'rouge' else 0}"/>
  <g clip-path="url(#f)" fill="none" stroke="{ombre}" stroke-width="12">
    <path d="{cerclage(y1)}"/><path d="{cerclage(y2)}"/>
  </g>
  <ellipse cx="{CX}" cy="{H}" rx="{RX}" ry="{RY}" fill="{couvercle}"/>
  <ellipse cx="{CX}" cy="{H}" rx="{RX - 11}" ry="{RY - 6}" fill="none" stroke="{rebord}" stroke-width="4"/>
  <ellipse cx="{CX + 42}" cy="{H - 3}" rx="13" ry="5.5" fill="{rebord}"/>
</g>
</svg>'''


if __name__ == "__main__":
    open("icon.svg", "w").write(svg(echelle=1.15))                      # favicon et icône « any »
    open("icon-maskable.svg", "w").write(svg(fond_plein=True, echelle=0.95))  # Android, zone sûre de 80 %
    open("apple-touch-icon.svg", "w").write(svg(fond_plein=True, echelle=1.08))  # iOS arrondit lui-même
