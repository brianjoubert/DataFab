"""Application icon, drawn with QPainter (no image files needed at runtime)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap


def render_icon(size: int = 256) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    # rounded tile
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#2563eb")))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    # wafer with a flat at the top
    c, R = QPointF(128 * s, 134 * s), 88 * s
    # Qt arc angles: 0° = 3 o'clock, counter-clockwise. The flat sits at the top, between
    # 90°-half and 90°+half; draw the arc the long way round and close it with the flat.
    half = math.degrees(math.acos(0.86))
    box = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
    path = QPainterPath()
    path.arcMoveTo(box, 90 + half)
    path.arcTo(box, 90 + half, 360 - 2 * half)
    path.closeSubpath()
    p.setBrush(QBrush(QColor("white")))
    p.drawPath(path)
    # die grid coloured like the wafer map
    colors = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725", "#21918c"]
    d, gap = 26 * s, 6 * s
    k = 0
    for row in range(4):
        for col in range(4):
            x = c.x() - 2 * d - 1.5 * gap + col * (d + gap)
            y = c.y() - 2 * d - 1.5 * gap + row * (d + gap) + 6 * s
            corner = (row in (0, 3)) and (col in (0, 3))
            if corner:
                continue
            p.setBrush(QBrush(QColor(colors[k % len(colors)])))
            p.drawRoundedRect(QRectF(x, y, d, d), 4 * s, 4 * s)
            k += 1
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render_icon(sz)))
    return icon
