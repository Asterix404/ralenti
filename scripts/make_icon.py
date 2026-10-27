"""Generates ralenti/resources/ralenti.ico: an italic R with two slow-motion echoes."""
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QTransform)

app = QGuiApplication(sys.argv)
out = Path(__file__).resolve().parents[1] / "ralenti" / "resources" / "ralenti.ico"
out.parent.mkdir(parents=True, exist_ok=True)
S, ACCENT = 256, QColor("#2dd4bf")

img = QImage(S, S, QImage.Format_ARGB32); img.fill(Qt.transparent)
p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)

tile = QPainterPath(); tile.addRoundedRect(QRectF(8, 8, 240, 240), 58, 58)
bg = QLinearGradient(0, 0, S, S)
bg.setColorAt(0, QColor("#1c2431")); bg.setColorAt(1, QColor("#0a0f12"))
p.fillPath(tile, bg)
p.setPen(QPen(QColor(255, 255, 255, 24), 2)); p.drawPath(tile)
p.setClipPath(tile)

font = QFont(); font.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
font.setPixelSize(188); font.setWeight(QFont.Black)
glyph = QPainterPath(); glyph.addText(0, 0, font, "R")
c = glyph.boundingRect().center()
t = QTransform(); t.translate(150, 128); t.shear(-0.2, 0); t.translate(-c.x(), -c.y())
glyph = t.map(glyph)

for dx, alpha in ((-40, 38), (-20, 90)):          # motion echoes
    ghost = QColor(ACCENT); ghost.setAlpha(alpha)
    p.fillPath(glyph.translated(dx, 0), ghost)
fill = QLinearGradient(0, 40, 0, 216)
fill.setColorAt(0, ACCENT.lighter(130)); fill.setColorAt(1, ACCENT)
p.fillPath(glyph, fill)
p.end()
print("Saved" if img.save(str(out)) else "FAILED", out)