import sys

from PyQt5.QtCore import (Qt, QRectF, QPoint, QPropertyAnimation, QParallelAnimationGroup,
                          QEasingCurve, QSize, QRect)
from PyQt5.QtGui import QHideEvent, QPainter, QPainterPath, QPen, QFont, QPixmap, QColor
from PyQt5.QtWidgets import (QWidget, QApplication, QMainWindow, QHBoxLayout,
                             QLabel, QVBoxLayout, QGridLayout, QFrame, QGraphicsDropShadowEffect)

from app.lol.aram import AramBuff
from app.common.qfluentwidgets import (ProgressRing, ToolTipFilter, ToolTipPosition, isDarkTheme,
                                       themeColor, FlyoutViewBase, TextWrap, FlyoutAnimationType)
from app.components.color_label import ColorLabel
from app.components.tool_tip import CustomToolTip
from app.common.style_sheet import StyleSheet


class ProgressArc(ProgressRing):
    def __init__(self, parent=None, useAni=True, text="", fontSize=10):
        self.text = text
        self.fontSize = fontSize
        self.drawVal = 0
        self.ringGap = 30
        super().__init__(parent, useAni=useAni)

    def paintEvent(self, e):
        # 有值取值, 没值保持; self.val 在控件刚实例化时, 前几次update可能会为0
        self.drawVal = self.val or self.drawVal
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)

        cw = self._strokeWidth  # circle thickness
        w = min(self.height(), self.width()) - cw
        rc = QRectF(cw / 2, self.height() / 2 - w / 2, w, w)

        # draw background
        bc = self.darkBackgroundColor if isDarkTheme() else self.lightBackgroundColor
        pen = QPen(bc, cw, cap=Qt.RoundCap, join=Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawArc(rc, (self.ringGap-90)*16, (360-2*self.ringGap)*16)

        if self.maximum() <= self.minimum():
            return

        # draw bar
        pen.setColor(themeColor())
        painter.setPen(pen)
        degree = int(self.drawVal / (self.maximum() -
                     self.minimum()) * (360 - 2*self.ringGap))
        painter.drawArc(rc, -(self.ringGap + 90) * 16, -degree * 16)

        painter.setFont(QFont('Microsoft YaHei', self.fontSize, QFont.Bold))
        text_rect = QRectF(0, self.height() * 0.88,
                           self.width(), self.height() * 0.12)

        painter.drawText(text_rect, Qt.AlignCenter, f"{self.text}")


class RoundLevelAvatar(QWidget):
    def __init__(self,
                 icon,
                 xpSinceLastLevel,
                 xpUntilNextLevel,
                 diameter=100,
                 text="",
                 aramInfo=None,
                 parent=None):
        super().__init__(parent)
        self.diameter = diameter
        self.sep = .3 * diameter
        self.iconPath = icon

        self.image = QPixmap(self.iconPath)

        self.setFixedSize(self.diameter, self.diameter)

        self.xpSinceLastLevel = xpSinceLastLevel
        self.xpUntilNextLevel = xpUntilNextLevel
        self.progressRing = ProgressArc(
            self, text=text, fontSize=int(.09 * diameter))
        self.progressRing.setTextVisible(False)
        self.progressRing.setFixedSize(self.diameter, self.diameter)

        # self.setToolTip(f"Exp: {xpSinceLastLevel} / {xpUntilNextLevel}")
        self.installEventFilter(ToolTipFilter(self, 250, ToolTipPosition.TOP))
        self.paintXpSinceLastLevel = None
        self.paintXpUntilNextLevel = None
        self.callUpdate = False

        self.mFlyout = None
        self.aramInfo = aramInfo

    def paintEvent(self, event):
        if self.paintXpSinceLastLevel != self.xpSinceLastLevel or self.paintXpUntilNextLevel != self.xpUntilNextLevel or self.callUpdate:
            self.progressRing.setVal(self.xpSinceLastLevel * 100 //
                                     self.xpUntilNextLevel if self.xpSinceLastLevel != 0 else 1)
            self.paintXpUntilNextLevel = self.xpUntilNextLevel
            self.paintXpSinceLastLevel = self.xpSinceLastLevel
            self.callUpdate = False

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        size = (self.size() - QSize(int(self.sep), int(self.sep))) * \
            self.devicePixelRatioF()

        image = self.image
        if 'champion' in self.iconPath:
            width = image.width() - 10
            height = image.height() - 10
            image = image.copy(5, 5, width, height)

        scaledImage = image.scaled(size,
                                   Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)

        clipPath = QPainterPath()

        rect = QRectF(self.sep // 2, self.sep // 2,
                      self.width() - self.sep,
                      self.height() - self.sep)
        clipPath.addEllipse(rect)

        painter.setClipPath(clipPath)
        painter.drawPixmap(rect.toRect(), scaledImage)

    def updateIcon(self, icon: str, xpSinceLastLevel=None, xpUntilNextLevel=None, text=""):
        self.iconPath = icon
        self.image = QPixmap(self.iconPath)

        if xpSinceLastLevel is not None and xpUntilNextLevel is not None:
            self.xpSinceLastLevel = xpSinceLastLevel
            self.xpUntilNextLevel = xpUntilNextLevel

            # self.setToolTip(f"Exp: {xpSinceLastLevel} / {xpUntilNextLevel}")

        if text:
            self.progressRing.text = text

        self.callUpdate = True
        self.repaint()

    def updateAramInfo(self, info):
        self.aramInfo = info

        if not self.mFlyout or not info:
            return

        self.mFlyout.view.updateInfo(info)

    def enterEvent(self, a0):
        if not self.aramInfo or self.mFlyout:
            return

        view = AramFlyoutView(self.aramInfo)

        self.mFlyout = CustomToolTip(view, self)
        self.mFlyout.show()

        super().enterEvent(a0)

    def leaveEvent(self, a0):
        if not self.aramInfo or not self.mFlyout:
            return

        self.mFlyout.fadeOut()
        self.mFlyout = None


class AramFlyoutView(FlyoutViewBase):
    def __init__(self, info, parent=None):
        super().__init__(parent=parent)

        self.damageDealt = 0
        self.damageReceived = 0
        self.healingIncrease = 0
        self.shieldIncrease = 0
        self.abilityHaste = 0
        self.tenacity = 0
        self.description = ""
        self.catName = ""

        self.vBoxLayout = QVBoxLayout(self)
        self.gridBox = QGridLayout()

        self.titleLabel = QLabel(parent=self)  # 英雄名字(带称号)
        self.damageDealtLabel = QLabel(
            self.tr('Damage Dealt: '), parent=self)  # 造成伤害的权重
        self.damageReceivedLabel = QLabel(
            self.tr('Damage Received: '), parent=self)  # 受到伤害的权重
        self.healingIncreaseLabel = QLabel(
            self.tr('Healing Increase: '), parent=self)  # 治疗增益的权重
        self.shieldIncreaseLabel = QLabel(
            self.tr('Shield Increase: '), parent=self)  # 护盾增益的权重
        self.abilityHasteLabel = QLabel(
            self.tr('Ability Haste: '), parent=self)  # 技能急速的权重, 是正向属性, 值越大cd越短
        self.tenacityLabel = QLabel(
            self.tr('Tenacity: '), parent=self)  # 韧性的权重

        self.damageDealtValueLabel = ColorLabel(parent=self)  # 造成伤害的权重
        self.damageReceivedValueLabel = ColorLabel(parent=self)  # 受到伤害的权重
        self.healingIncreaseValueLabel = ColorLabel(parent=self)  # 治疗增益的权重
        self.shieldIncreaseValueLabel = ColorLabel(parent=self)  # 护盾增益的权重
        self.abilityHasteValueLabel = ColorLabel(
            parent=self)  # 技能急速的权重, 是正向属性, 值越大cd越短
        self.tenacityValueLabel = ColorLabel(parent=self)  # 韧性的权重

        self.descriptionLabel = QLabel(parent=self)  # 额外调整
        self.powerByLabel = QLabel(self.tr("Powered by: jddld.com"))

        self.updateInfo(info)

        self.__initWidgets()
        self.__initLayout()

        StyleSheet.ARAM_FLYOUT.apply(self)

    def __initWidgets(self):
        self.titleLabel.setObjectName("titleLabel")

        self.damageDealtLabel.setObjectName("damageDealtLabel")
        self.damageReceivedLabel.setObjectName("damageReceivedLabel")
        self.healingIncreaseLabel.setObjectName("healingIncreaseLabel")
        self.shieldIncreaseLabel.setObjectName("shieldIncreaseLabel")
        self.abilityHasteLabel.setObjectName("abilityHasteLabel")
        self.tenacityLabel.setObjectName("tenacityLabel")

        self.damageDealtValueLabel.setObjectName("damageDealtValueLabel")
        self.damageReceivedValueLabel.setObjectName("damageReceivedValueLabel")
        self.healingIncreaseValueLabel.setObjectName(
            "healingIncreaseValueLabel")
        self.shieldIncreaseValueLabel.setObjectName("shieldIncreaseValueLabel")
        self.abilityHasteValueLabel.setObjectName("abilityHasteValueLabel")
        self.tenacityValueLabel.setObjectName("tenacityValueLabel")

        self.descriptionLabel.setObjectName("descriptionLabel")
        self.powerByLabel.setObjectName("powerByLabel")

        self.titleLabel.setVisible(True)
        self.damageDealtLabel.setVisible(True)
        self.damageReceivedLabel.setVisible(True)
        self.healingIncreaseLabel.setVisible(True)
        self.shieldIncreaseLabel.setVisible(True)
        self.abilityHasteLabel.setVisible(True)
        self.tenacityLabel.setVisible(True)

        self.titleLabel.setAlignment(Qt.AlignCenter)
        self.powerByLabel.setAlignment(Qt.AlignCenter)
        self.descriptionLabel.setWordWrap(True)

    def __initLayout(self):
        self.vBoxLayout.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        self.vBoxLayout.setContentsMargins(16, 12, 16, 12)
        self.vBoxLayout.setSpacing(16)
        self.gridBox.setHorizontalSpacing(20)
        self.gridBox.setVerticalSpacing(4)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addLayout(self.gridBox)
        self.vBoxLayout.addWidget(self.descriptionLabel)
        self.vBoxLayout.addWidget(self.powerByLabel)

        self.gridBox.addWidget(self.damageDealtLabel, 0, 0, Qt.AlignLeft)
        self.gridBox.addWidget(self.damageDealtValueLabel, 0, 1, Qt.AlignRight)
        self.gridBox.addWidget(self.damageReceivedLabel, 0, 2, Qt.AlignLeft)
        self.gridBox.addWidget(
            self.damageReceivedValueLabel, 0, 3, Qt.AlignRight)
        self.gridBox.addWidget(self.healingIncreaseLabel, 1, 0, Qt.AlignLeft)
        self.gridBox.addWidget(
            self.healingIncreaseValueLabel, 1, 1, Qt.AlignRight)
        self.gridBox.addWidget(self.shieldIncreaseLabel, 1, 2, Qt.AlignLeft)
        self.gridBox.addWidget(
            self.shieldIncreaseValueLabel, 1, 3, Qt.AlignRight)
        self.gridBox.addWidget(self.abilityHasteLabel, 2, 0, Qt.AlignLeft)
        self.gridBox.addWidget(
            self.abilityHasteValueLabel, 2, 1, Qt.AlignRight)
        self.gridBox.addWidget(self.tenacityLabel, 2, 2, Qt.AlignLeft)
        self.gridBox.addWidget(self.tenacityValueLabel, 2, 3, Qt.AlignRight)

    def __updateStyle(self):
        """
        数据更新时调用一下, 把样式更新
        """
        self.descriptionLabel.setVisible(bool(self.description))

        self.damageDealtValueLabel.setType(
            self.__getColor(self.damageDealt, 100))
        self.damageReceivedValueLabel.setType(
            self.__getColor(self.damageReceived, 100, False))
        self.healingIncreaseValueLabel.setType(
            self.__getColor(self.healingIncrease, 100))
        self.shieldIncreaseValueLabel.setType(
            self.__getColor(self.shieldIncrease, 100))
        self.abilityHasteValueLabel.setType(
            self.__getColor(self.abilityHaste, 0))
        self.tenacityValueLabel.setType(
            self.__getColor(self.tenacity, 0))

    def __getColor(self, val, criterion, isPositive=True) -> str:
        """
        isPositive: 用于标记该属性是否积极(越大越好)
        """
        # 如果值越小越好, 交换一下条件, 让低的标记为绿色
        if not isPositive:
            val, criterion = criterion, val

        if val > criterion:
            return 'win'  # 绿色
        elif val < criterion:
            return 'lose'  # 红色

        return "text"

    def updateInfo(self, info):
        self.catName = info.get("catname", "").replace("-", " - ")
        self.damageDealt = int(info.get("zcsh", "100"))
        self.damageReceived = int(info.get("sdsh", "100"))
        self.healingIncrease = int(info.get("zlxl", "100"))
        self.shieldIncrease = int(info.get("hdxn", "100"))
        self.abilityHaste = int(info.get("jnjs", "0"))
        self.tenacity = int(info.get("renxing", "0"))
        self.description = info.get("description", "")

        if self.description:
            self.description = self.description.replace(
                "(", "（").replace(")", "）")
            self.descriptionLabel.setText(self.description)

        self.titleLabel.setText(self.catName)
        self.damageDealtValueLabel.setText(f"{self.damageDealt}%")
        self.damageReceivedValueLabel.setText(f"{self.damageReceived}%")
        self.healingIncreaseValueLabel.setText(f"{self.healingIncrease}%")
        self.shieldIncreaseValueLabel.setText(f"{self.shieldIncrease}%")
        self.abilityHasteValueLabel.setText(f"{self.abilityHaste}")
        self.tenacityValueLabel.setText(f"{self.tenacity}")

        self.__updateStyle()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = QMainWindow()
    window.setWindowTitle("Round Icon Demo")
    window.setGeometry(100, 100, 600, 400)

    widget = QWidget(window)
    window.setCentralWidget(widget)

    layout = QHBoxLayout(widget)

    icon1 = RoundLevelAvatar("../resource/images/logo.png",
                             75,
                             100,
                             diameter=70)
    icon1.setParent(window)

    layout.addWidget(icon1)
    window.show()
    sys.exit(app.exec())
