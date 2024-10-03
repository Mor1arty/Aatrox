import re
import threading
import time
from asyncio import CancelledError, LifoQueue
from collections import OrderedDict

import pyperclip
from qasync import asyncSlot
import asyncio
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QFrame,
                             QSpacerItem, QSizePolicy, QLabel, QStackedWidget, QWidget)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QColor

from ..common.logger import logger
from ..common.qfluentwidgets import (SmoothScrollArea, PushButton, ToolButton, InfoBar,
                                     InfoBarPosition, ToolTipFilter, ToolTipPosition,
                                     isDarkTheme, FlyoutViewBase, Flyout, Theme,
                                     IndeterminateProgressRing, ComboBox, StateToolTip)

from app.common.style_sheet import StyleSheet, ColorChangeable
from app.common.icons import Icon
from app.common.config import cfg
from app.common.signals import signalBus
from app.components.champion_icon_widget import RoundIcon, RoundedLabel
from app.components.search_line_edit import SearchLineEdit
from app.components.summoner_name_button import SummonerName
from app.components.animation_frame import ColorAnimationFrame, CardWidget
from app.components.color_label import ColorLabel
from app.lol.connector import connector
from app.lol.exceptions import SummonerGamesNotFound, SummonerNotFound
from app.lol.tools import parseGameData, parseGameDetailData, parseGamesDataConcurrently
from ..components.seraphine_interface import SeraphineInterface


TAG = "SearchInterface"


def asyncLockDecorator(lockName):
    """
    给任何一个方法加锁, return时释放
    args[0]是self, 通过getattr找到lockName
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            lock = getattr(args[0], lockName)
            async with lock:
                return await func(*args, **kwargs)
        return wrapper
    return decorator


class GamesTab(QFrame):
    tabClicked = pyqtSignal(str)
    gameDetailReady = pyqtSignal(dict)
    loadFinish = pyqtSignal()

    def __init__(self, parnet=None):
        super().__init__(parnet)
        self.setFixedWidth(160)
        self.vBoxLayout = QVBoxLayout(self)

        self.stackWidget = QStackedWidget()
        self.buttonsLayout = QHBoxLayout()

        self.prevButton = ToolButton(Icon.CHEVRONLEFT)
        self.pageLabel = QLabel(" ")
        self.nextButton = ToolButton(Icon.CHEVRONRIGHT)

        # 从生涯界面里调过来，需要被选中但还没被选中的 gameId
        self.waitingForSelect = None

        # 当前选中的 queueId，-1 对应所有对局
        self.queueId = -1

        # 当前所在的页码
        self.currentPageNum = 0

        # 目前已经绘制好的最大页码
        self.maxPageNum = 0

        # 目前选中的 tab
        self.currentTabSelected = None

        # 所有的对局记录
        self.games = LifoQueue()  # LifoQueue 保证线程安全 -- By Hpero4

        # queueId 到对应 self.games 下标数组的映射
        # OrderedDict 保证线程安全 -- By Hpero4
        self.queueIdMap = OrderedDict({-1: []})

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.pageLabel.setAlignment(Qt.AlignCenter)
        self.prevButton.setVisible(False)
        self.prevButton.setEnabled(False)
        self.nextButton.setVisible(False)
        self.nextButton.setEnabled(False)

    def __initLayout(self):
        defaultWidget = QWidget()
        layout = QVBoxLayout(defaultWidget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stackWidget.addWidget(defaultWidget)
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.buttonsLayout.addWidget(self.prevButton)
        self.buttonsLayout.addWidget(self.pageLabel)
        self.buttonsLayout.addWidget(self.nextButton)

        self.vBoxLayout.addWidget(self.stackWidget)
        self.vBoxLayout.addSpacing(10)
        self.vBoxLayout.addLayout(self.buttonsLayout)

    def __connectSignalToSlot(self):
        self.prevButton.clicked.connect(self.__onPrevButtonClicked)
        self.nextButton.clicked.connect(self.__onNextButtonClicked)

    def updateQueueIdMap(self, games):
        for game in games:
            index = len(self.games)
            self.games.append(game)
            queueId = game['queueId']

            l: list = self.queueIdMap.get(queueId)

            if not l:
                self.queueIdMap[queueId] = [index]
            else:
                l.append(index)

            self.queueIdMap[-1].append(index)

    @asyncSlot()
    async def __onPrevButtonClicked(self):
        self.currentPageNum -= 1
        self.pageLabel.setText(f"{self.currentPageNum}")
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.resetButtonEnabled()

    @asyncSlot()
    async def __onNextButtonClicked(self):
        self.currentPageNum += 1

        if self.currentPageNum > self.maxPageNum:
            # self.games 一定足够绘制下一页，因为否则这个按钮本身就点不了
            self.prepareNextPage()

        self.pageLabel.setText(f"{self.currentPageNum}")
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.resetButtonEnabled()

    def showTheFirstPage(self):
        self.prepareNextPage()

        self.stackWidget.setCurrentIndex(1)
        self.currentPageNum = 1
        self.pageLabel.setText("1")

        self.nextButton.setVisible(True)
        self.prevButton.setVisible(True)
        self.pageLabel.setVisible(True)

        self.resetButtonEnabled()

    def resetButtonEnabled(self):
        prevEnable = not self.currentPageNum in [0, 1]
        self.prevButton.setEnabled(prevEnable)

        nextEnable = len(
            self.queueIdMap.get(self.queueId, [])) > self.currentPageNum * 10
        self.nextButton.setEnabled(nextEnable)

    def prepareNextPage(self):
        ''' 满足
        - 当前所在页码 == 已经绘制好的最大页码时
        - 在内存中的对局记录数量大于已经绘制好的数量时

        调用该函数，绘制下一页，加入 stackedWidget
        '''

        # 游戏数据在 self.games 数组中对应的下标
        indices = self.queueIdMap[self.queueId]

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        begin = self.maxPageNum * 10

        # 防止能画上去的不够 10 个导致越界
        end = min(10 + begin, len(indices))

        for i in range(begin, end):
            tab = GameTab(self.games[indices[i]])
            waiting = self.waitingForSelect

            if waiting and tab.gameId == int(self.waitingForSelect):
                tab.setProperty('selected', True)
                tab.style().polish(tab)
                self.currentTabSelected = tab
                self.waitingForSelect = None

            layout.addWidget(tab, stretch=1)

        # 如果不够十个就给它填充一下
        if end - begin < 10:
            layout.addStretch(10 - (end - begin))
            spacing = layout.spacing()
            layout.addSpacing((10 - (end - begin)) * spacing)

        self.stackWidget.addWidget(widget)
        self.maxPageNum += 1

    def clearTabs(self):
        self.currentPageNum = 0
        self.maxPageNum = 0
        self.currentTabSelected = None

        for i in reversed(range(len(self.stackWidget))):
            if i == 0:
                continue

            widget = self.stackWidget.widget(i)
            self.stackWidget.removeWidget(widget)

        self.stackWidget.setCurrentIndex(0)

    def clear(self):
        self.pageLabel.setText(" ")

        self.prevButton.setVisible(False)
        self.nextButton.setVisible(False)

        self.queueId = -1
        self.games = []
        self.queueIdMap = {-1: []}

        self.clearTabs()

    def clickFirstTab(self):
        widget = self.stackWidget.widget(1)
        tab = widget.layout().itemAt(0).widget()

        tab.setProperty("pressed", False)
        tab.setProperty("selected", True)
        tab.style().polish(tab)
        signalBus.gameTabClicked.emit(tab)


class GameDetailView(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)
        self.stackedWidget = QStackedWidget()

        self.infoPage = QWidget()
        self.vBoxLayout = QVBoxLayout(self.infoPage)
        self.titleBar = GameTitleBar()

        self.scrollWidget = QWidget()
        self.scrollArea = SmoothScrollArea()
        self.scrollLayout = QVBoxLayout()

        self.teamView1 = TeamView()
        self.teamView2 = TeamView()

        self.extraTeamView1 = TeamView()
        self.extraTeamView2 = TeamView()
        self.extraTeamView3 = TeamView()
        self.extraTeamView4 = TeamView()
        self.extraTeamView5 = TeamView()
        self.extraTeamView6 = TeamView()

        self.loadingPage = QWidget()
        self.loadingPageLayout = QHBoxLayout(self.loadingPage)
        self.processRing = IndeterminateProgressRing()

        self.__initLayout()

    def clear(self):
        for i in reversed(range(self.vBoxLayout.count())):
            item = self.vBoxLayout.itemAt(i)
            self.vBoxLayout.removeItem(item)

            if item.widget():
                item.widget().deleteLater()

        self.titleBar = GameTitleBar()

        self.scrollWidget = QWidget()
        self.scrollArea = SmoothScrollArea()
        self.scrollLayout = QVBoxLayout()

        self.teamView1 = TeamView()
        self.teamView2 = TeamView()

        self.extraTeamView1 = TeamView()
        self.extraTeamView2 = TeamView()
        self.extraTeamView3 = TeamView()
        self.extraTeamView4 = TeamView()
        self.extraTeamView5 = TeamView()
        self.extraTeamView6 = TeamView()

        self.__initLayout()

    def __initLayout(self):
        self.scrollArea.setObjectName("gameScrollArea")
        # self.scrollArea.setVerticalScrollBarPolicy(
        #     Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollWidget.setObjectName("scrollWidget")

        self.scrollWidget.setLayout(self.scrollLayout)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)

        self.loadingPageLayout.addWidget(self.processRing)

        self.vBoxLayout.addWidget(self.titleBar)

        self.scrollLayout.setContentsMargins(0, 0, 0, 0)
        self.scrollLayout.addWidget(self.teamView1)
        self.scrollLayout.addWidget(self.teamView2)

        self.scrollLayout.addWidget(self.extraTeamView1)
        self.scrollLayout.addWidget(self.extraTeamView2)
        self.scrollLayout.addWidget(self.extraTeamView3)
        self.scrollLayout.addWidget(self.extraTeamView4)
        self.scrollLayout.addWidget(self.extraTeamView5)
        self.scrollLayout.addWidget(self.extraTeamView6)

        self.vBoxLayout.addWidget(self.scrollArea)

        self.extraTeamView1.setVisible(False)
        self.extraTeamView2.setVisible(False)
        self.extraTeamView3.setVisible(False)
        self.extraTeamView4.setVisible(False)
        self.extraTeamView5.setVisible(False)
        self.extraTeamView6.setVisible(False)

        self.stackedWidget.addWidget(self.loadingPage)
        self.stackedWidget.addWidget(self.infoPage)
        self.stackedWidget.setCurrentIndex(1)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addWidget(self.stackedWidget)

        # self.vBoxLayout.addSpacerItem(
        #     QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def setLoadingPageEnabled(self, enable: bool):
        index = 0 if enable else 1
        self.stackedWidget.setCurrentIndex(index)

    def updateGame(self, game):
        if not game:
            InfoBar.error(self.tr("Get game infomation failed"),
                          self.tr("The server returned abnormal content."),
                          orient=Qt.Vertical,
                          duration=5000,
                          position=InfoBarPosition.BOTTOM_RIGHT,
                          parent=self.window())

            return

        isCherry = game["queueId"] == 1700
        self.titleBar.updateTitleBar(game)

        team1 = game["teams"][100]
        team2 = game["teams"][200]

        self.teamView1.updateTeam(team1, isCherry, self.tr("1st"))
        self.teamView1.updateSummoners(team1["summoners"])

        self.teamView2.updateTeam(team2, isCherry, self.tr("2nd"))
        self.teamView2.updateSummoners(team2["summoners"])

        self.extraTeamView1.setVisible(isCherry)
        self.extraTeamView2.setVisible(isCherry)
        self.extraTeamView3.setVisible(isCherry)
        self.extraTeamView4.setVisible(isCherry)
        self.extraTeamView5.setVisible(isCherry)
        self.extraTeamView6.setVisible(isCherry)

        if isCherry:
            team3 = game["teams"][300]
            team4 = game["teams"][400]
            team5 = game["teams"][500]
            team6 = game["teams"][600]
            team7 = game["teams"][700]
            team8 = game["teams"][800]

            self.extraTeamView1.updateTeam(team3, isCherry, self.tr("3rd"))
            self.extraTeamView1.updateSummoners(team3["summoners"])

            self.extraTeamView2.updateTeam(team4, isCherry, self.tr("4th"))
            self.extraTeamView2.updateSummoners(team4["summoners"])

            self.extraTeamView3.updateTeam(team5, isCherry, self.tr("5th"))
            self.extraTeamView3.updateSummoners(team5["summoners"])

            self.extraTeamView4.updateTeam(team6, isCherry, self.tr("6th"))
            self.extraTeamView4.updateSummoners(team6["summoners"])

            self.extraTeamView5.updateTeam(team7, isCherry, self.tr("7th"))
            self.extraTeamView5.updateSummoners(team7["summoners"])

            self.extraTeamView6.updateTeam(team8, isCherry, self.tr("8th"))
            self.extraTeamView6.updateSummoners(team8["summoners"])


class TeamView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.titleBarLayout = QHBoxLayout()
        self.summonersLayout = QVBoxLayout()

        self.teamResultLabel = ColorLabel()
        self.towerIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.towerKillsLabel = QLabel()
        self.inhibitorIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.inhibitorKillsLabel = QLabel()
        self.baronIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.baronKillsLabel = QLabel()
        self.dragonIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.dragonKillsLabel = QLabel()
        self.riftHeraldIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.riftHeraldKillsLabel = QLabel()

        self.bansButton = PushButton("Bans")
        self.csIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.goldIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.dmgIconLabel = QLabel()
        self.kdaLabel = QLabel()

        self.isToolTipInit = False

        self.bansFlyOut = None
        self.bansInfo = None

        self.__initWidget()
        self.__initLayout()

        cfg.themeChanged.connect(self.__updateIconColor)

    def __initWidget(self):
        self.teamResultLabel.setObjectName("teamResult")

        self.towerIconLabel.setFixedSize(22, 22)
        self.inhibitorIconLabel.setFixedSize(18, 18)
        self.baronIconLabel.setFixedSize(17, 17)
        self.dragonIconLabel.setFixedSize(18, 18)
        self.riftHeraldIconLabel.setFixedSize(18, 18)

        self.bansButton.setVisible(False)

        self.kdaLabel.setFixedWidth(100)
        self.kdaLabel.setAlignment(Qt.AlignCenter)
        self.csIconLabel.setFixedSize(18, 18)
        self.goldIconLabel.setFixedSize(18, 18)
        self.dmgIconLabel.setFixedWidth(70)
        self.dmgIconLabel.setAlignment(Qt.AlignCenter)

        self.csIconLabel.setVisible(False)
        self.goldIconLabel.setVisible(False)

        self.dmgIconLabel.setObjectName("dmgIconLabel")
        self.bansButton.clicked.connect(self.__onBansButtonClicked)

    def __initToolTip(self):
        self.towerIconLabel.setToolTip(self.tr("Tower destroyed"))
        self.towerIconLabel.setAlignment(Qt.AlignCenter)
        self.inhibitorIconLabel.setToolTip(self.tr("Inhibitor destroyed"))
        self.inhibitorIconLabel.setAlignment(Qt.AlignCenter)
        self.baronIconLabel.setToolTip(self.tr("Baron Nashor killed"))
        self.baronIconLabel.setAlignment(Qt.AlignCenter)
        self.dragonIconLabel.setToolTip(self.tr("Dragon killed"))
        self.dragonIconLabel.setAlignment(Qt.AlignCenter)
        self.riftHeraldIconLabel.setToolTip(self.tr("Rift Herald killed"))
        self.riftHeraldIconLabel.setAlignment(Qt.AlignCenter)

        self.towerIconLabel.installEventFilter(ToolTipFilter(
            self.towerIconLabel, 500, ToolTipPosition.TOP))
        self.inhibitorIconLabel.installEventFilter(ToolTipFilter(
            self.inhibitorIconLabel, 500, ToolTipPosition.TOP))
        self.baronIconLabel.installEventFilter(ToolTipFilter(
            self.baronIconLabel, 500, ToolTipPosition.TOP))
        self.dragonIconLabel.installEventFilter(ToolTipFilter(
            self.dragonIconLabel, 500, ToolTipPosition.TOP))
        self.riftHeraldIconLabel.installEventFilter(ToolTipFilter(
            self.riftHeraldIconLabel, 500, ToolTipPosition.TOP))

        self.towerKillsLabel.setToolTip(self.tr("Tower destroyed"))
        self.inhibitorKillsLabel.setToolTip(self.tr("Inhibitor destroyed"))
        self.baronKillsLabel.setToolTip(self.tr("Baron Nashor killed"))
        self.dragonKillsLabel.setToolTip(self.tr("Dragon killed"))
        self.riftHeraldKillsLabel.setToolTip(self.tr("Rift Herald killed"))

        self.towerKillsLabel.installEventFilter(ToolTipFilter(
            self.towerKillsLabel, 500, ToolTipPosition.TOP))
        self.inhibitorKillsLabel.installEventFilter(ToolTipFilter(
            self.inhibitorKillsLabel, 500, ToolTipPosition.TOP))
        self.baronKillsLabel.installEventFilter(ToolTipFilter(
            self.baronKillsLabel, 500, ToolTipPosition.TOP))
        self.dragonKillsLabel.installEventFilter(ToolTipFilter(
            self.dragonKillsLabel, 500, ToolTipPosition.TOP))
        self.riftHeraldKillsLabel.installEventFilter(ToolTipFilter(
            self.riftHeraldKillsLabel, 500, ToolTipPosition.TOP))

        self.csIconLabel.setToolTip(self.tr("Minions killed"))
        self.goldIconLabel.setToolTip(self.tr("Gold earned"))
        self.dmgIconLabel.setToolTip(self.tr("Damage dealed to champions"))
        self.csIconLabel.installEventFilter(ToolTipFilter(
            self.csIconLabel, 500, ToolTipPosition.TOP))
        self.goldIconLabel.installEventFilter(ToolTipFilter(
            self.goldIconLabel, 500, ToolTipPosition.TOP))
        self.dmgIconLabel.installEventFilter(ToolTipFilter(
            self.dmgIconLabel, 500, ToolTipPosition.TOP))

    def __initLayout(self):
        self.teamResultLabel.setFixedHeight(39)
        self.teamResultLabel.setFixedWidth(60)

        self.titleBarLayout.setSpacing(0)
        self.titleBarLayout.addWidget(self.teamResultLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.towerIconLabel)
        self.titleBarLayout.addWidget(self.towerKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.inhibitorIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.inhibitorKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.baronIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.baronKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.dragonIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.dragonKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.riftHeraldIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.riftHeraldKillsLabel)
        self.titleBarLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.titleBarLayout.addWidget(self.bansButton)
        self.titleBarLayout.addSpacing(59)
        self.titleBarLayout.addWidget(self.kdaLabel)
        self.titleBarLayout.addSpacing(24)
        self.titleBarLayout.addWidget(self.csIconLabel)
        self.titleBarLayout.addSpacing(43)
        self.titleBarLayout.addWidget(self.goldIconLabel)
        self.titleBarLayout.addSpacing(28)
        self.titleBarLayout.addWidget(self.dmgIconLabel)
        self.titleBarLayout.addSpacing(7)

        self.summonersLayout.setContentsMargins(0, 0, 0, 0)
        self.summonersLayout.setSpacing(4)

        self.vBoxLayout.setContentsMargins(7, 0, 7, 6)
        self.vBoxLayout.addLayout(self.titleBarLayout)
        self.vBoxLayout.addLayout(self.summonersLayout)
        # self.vBoxLayout.addSpacerItem(
        #     QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def updateTeam(self, team, isCherry, result):
        if not self.isToolTipInit:
            self.isToolTipInit = True
            self.__initToolTip()

        win = team['win']
        baronIcon = team['baronIcon']
        baronKills = team['baronKills']
        dragonIcon = team['dragonIcon']
        dragonKills = team['dragonKills']
        riftHeraldIcon = team['riftHeraldIcon']
        riftHeraldKills = team['riftHeraldKills']
        inhibitorIcon = team['inhibitorIcon']
        inhibitorKills = team['inhibitorKills']
        towerIcon = team['towerIcon']
        towerKills = team['towerKills']
        kills = team['kills']
        deaths = team['deaths']
        assists = team['assists']
        bans = team['bans']

        if isCherry:
            self.teamResultLabel.setText(result)
        elif win == "Win":
            self.teamResultLabel.setText(self.tr("Winner"))
            self.teamResultLabel.setType('win')
        else:
            self.teamResultLabel.setText(self.tr("Loser"))
            self.teamResultLabel.setType('lose')

        self.towerKillsLabel.setText(str(towerKills))
        self.inhibitorKillsLabel.setText(str(inhibitorKills))
        self.baronKillsLabel.setText(str(baronKills))
        self.dragonKillsLabel.setText(str(dragonKills))
        self.riftHeraldKillsLabel.setText(str(riftHeraldKills))

        self.towerIconLabel.setPicture(towerIcon)
        self.inhibitorIconLabel.setPicture(inhibitorIcon)
        self.baronIconLabel.setPicture(baronIcon)
        self.dragonIconLabel.setPicture(dragonIcon)
        self.riftHeraldIconLabel.setPicture(riftHeraldIcon)

        self.dmgIconLabel.setText("DMG")

        color = "white" if isDarkTheme() else "black"
        self.goldIconLabel.setPicture(f"app/resource/images/Gold_{color}.png")
        self.csIconLabel.setPicture(f"app/resource/images/Minions_{color}.png")

        if len(bans) != 0:
            self.bansButton.setVisible(True)
            self.bansInfo = bans

            if self.bansFlyOut:
                self.bansFlyOut.close()
        else:
            self.bansButton.setVisible(False)
            self.bansInfo = None

        self.csIconLabel.setVisible(True)
        self.goldIconLabel.setVisible(True)

        self.kdaLabel.setText(f"{kills} / {deaths} / {assists}")

    def updateSummoners(self, summoners):
        for i in reversed(range(self.summonersLayout.count())):
            item = self.summonersLayout.itemAt(i)
            self.summonersLayout.removeItem(item)
            if item.widget():
                item.widget().deleteLater()

        for summoner in summoners:
            infoBar = SummonerInfoBar(summoner)

            self.summonersLayout.addWidget(infoBar)

        if len(summoners) != 5:
            self.summonersLayout.addSpacerItem(QSpacerItem(
                1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def __updateIconColor(self, theme: Theme):
        color = "white" if theme == Theme.DARK else "black"
        self.goldIconLabel.setPicture(f"app/resource/images/Gold_{color}.png")
        self.csIconLabel.setPicture(f"app/resource/images/Minions_{color}.png")

    def __onBansButtonClicked(self):
        flyout = BansFlyoutView(self.bansInfo)
        self.bansFlyOut = Flyout.make(flyout, self.bansButton, self)


class BansFlyoutView(FlyoutViewBase):
    def __init__(self, bans, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)

        for champion in bans:
            icon = RoundIcon(champion, 25, 0, 3)
            self.hBoxLayout.addWidget(icon)


class SummonerInfoBar(CardWidget):
    def __init__(self, summoner, parent=None):
        super().__init__(parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor

        self.setFixedHeight(39)

        self.hBoxLayout = QHBoxLayout(self)
        self.runeIcon = RoundedLabel(radius=0, borderWidth=0)

        self.spellsLayout = QHBoxLayout()

        self.spell1Icon = RoundedLabel(radius=0, borderWidth=1)
        self.spell2Icon = RoundedLabel(radius=0, borderWidth=1)

        self.levelLabel = QLabel()
        self.championIconLabel = RoundIcon(summoner["championIcon"], 25, 0, 3)
        self.summonerName = SummonerName(
            summoner["summonerName"], isPublic=summoner["isPublic"])

        self.rankIcon = RoundedLabel(radius=0, borderWidth=0)

        self.itemsLayout = QHBoxLayout()
        self.items = []

        self.kdaLabel = QLabel()
        self.csLabel = QLabel()
        self.goldLabel = QLabel()
        self.demageLabel = QLabel()

        self.__initWidget(summoner)
        self.__initLayout()

        self.summonerName.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(summoner["puuid"]))

    def __initWidget(self, summoner):
        self.isCurrent = summoner["isCurrent"]
        if self.isCurrent:
            self.setObjectName("currentSummonerWidget")

        self.summonerName.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.summonerName.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.runeIcon.setPicture(QPixmap(summoner["runeIcon"]))
        self.runeIcon.setFixedSize(23, 23)
        self.spell1Icon.setFixedSize(18, 18)
        self.spell1Icon.setPicture(summoner["spell1Icon"])
        self.spell2Icon.setFixedSize(18, 18)
        self.spell2Icon.setPicture(summoner["spell2Icon"])

        self.levelLabel.setText(str(summoner["champLevel"]))
        self.levelLabel.setObjectName("levelLabel")
        self.levelLabel.setAlignment(Qt.AlignCenter)
        self.levelLabel.setFixedWidth(20)

        self.items = []
        for icon in summoner['itemIcons']:
            label = RoundedLabel(icon, 0)
            label.setFixedSize(23, 23)
            self.items.append(label)

        if summoner["rankInfo"]:
            if summoner['rankIcon'] != None and summoner['rankIcon'] != '':
                self.rankIcon.setPicture(summoner["rankIcon"])
                self.rankIcon.setFixedSize(30, 30)

                tier, divison, lp = summoner["tier"], summoner["division"], summoner["lp"]
                if tier != "":
                    self.rankIcon.setToolTip(f"{tier} {divison} {lp}")
                else:
                    self.rankIcon.setToolTip(self.tr("Unranked"))

                self.rankIcon.installEventFilter(
                    ToolTipFilter(self.rankIcon, 0, ToolTipPosition.TOP))
            else:
                self.rankIcon.setText(str(summoner['lp']))
                self.rankIcon.setFixedWidth(40)
                self.rankIcon.setAlignment(Qt.AlignCenter)

        self.kdaLabel.setText(
            f"{summoner['kills']} / {summoner['deaths']} / {summoner['assists']}")
        self.kdaLabel.setFixedWidth(100)
        self.kdaLabel.setAlignment(Qt.AlignCenter)

        self.csLabel.setText(str(summoner["cs"]))
        self.csLabel.setAlignment(Qt.AlignCenter)
        self.csLabel.setFixedWidth(50)

        self.goldLabel.setText(str(summoner["gold"]))
        self.goldLabel.setAlignment(Qt.AlignCenter)
        self.goldLabel.setFixedWidth(60)

        self.demageLabel.setText(str(summoner["demage"]))
        self.demageLabel.setAlignment(Qt.AlignCenter)
        self.demageLabel.setFixedWidth(70)

    def __initLayout(self):
        self.spellsLayout.setContentsMargins(0, 0, 0, 0)
        self.spellsLayout.setSpacing(0)
        self.spellsLayout.addWidget(self.spell1Icon)
        self.spellsLayout.addWidget(self.spell2Icon)

        self.itemsLayout.setSpacing(0)
        self.spellsLayout.setContentsMargins(0, 0, 0, 0)

        for icon in self.items:
            self.itemsLayout.addWidget(icon)

        self.hBoxLayout.setContentsMargins(6, 0, 6, 0)
        self.hBoxLayout.addWidget(self.runeIcon)
        self.hBoxLayout.addLayout(self.spellsLayout)
        self.hBoxLayout.addWidget(self.levelLabel)
        self.hBoxLayout.addWidget(self.championIconLabel)
        self.hBoxLayout.addWidget(self.summonerName, alignment=Qt.AlignVCenter)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Minimum, QSizePolicy.Minimum))
        self.hBoxLayout.addSpacing(5)
        self.hBoxLayout.addWidget(self.rankIcon)
        self.hBoxLayout.addSpacing(5)
        self.hBoxLayout.addLayout(self.itemsLayout)

        self.hBoxLayout.addWidget(self.kdaLabel)
        self.hBoxLayout.addWidget(self.csLabel)
        self.hBoxLayout.addWidget(self.goldLabel)
        self.hBoxLayout.addWidget(self.demageLabel)


class GameTitleBar(QFrame, ColorChangeable):
    def __init__(self, type: str = None, parent=None):
        QFrame.__init__(self, parent=parent)
        ColorChangeable.__init__(self, type)

        self.setFixedHeight(78)
        self.titleBarLayout = QHBoxLayout(self)
        self.infoLayout = QVBoxLayout()

        self.mapIcon = RoundedLabel(borderWidth=0, radius=0)
        self.resultLabel = QLabel()
        self.infoLabel = QLabel()
        self.copyGameIdButton = ToolButton(Icon.COPY)
        self.gameId = None

        self.remake = None
        self.win = None

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.resultLabel.setObjectName("resultLabel")
        self.infoLabel.setObjectName("infoLabel")
        self.copyGameIdButton.setVisible(False)
        self.copyGameIdButton.setFixedSize(36, 36)
        self.copyGameIdButton.setToolTip(self.tr("Copy game ID"))
        self.copyGameIdButton.installEventFilter(ToolTipFilter(
            self.copyGameIdButton, 500, ToolTipPosition.LEFT))

    def __initLayout(self):
        self.infoLayout.setSpacing(0)
        self.infoLayout.setContentsMargins(0, 4, 0, 6)
        self.infoLayout.addSpacing(-5)
        self.infoLayout.addWidget(self.resultLabel)
        self.infoLayout.addWidget(self.infoLabel)

        self.titleBarLayout.addWidget(self.mapIcon)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addLayout(self.infoLayout)
        self.titleBarLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.titleBarLayout.addWidget(self.copyGameIdButton)
        self.titleBarLayout.addSpacing(10)

    def updateTitleBar(self, game):
        isCherry = game["queueId"] == 1700

        self.remake = game['remake']
        self.win = game['win']

        if game["remake"]:
            result = self.tr("Remake")
            self.setType('remake')
        elif game["win"]:
            result = self.tr("Win")
            self.setType('win')
        else:
            result = self.tr("Lose")
            self.setType('lose')

        if isCherry:
            cherryResult = game["cherryResult"]
            if cherryResult == 1:
                result = self.tr("1st")
            elif cherryResult == 2:
                result = self.tr("2nd")
            elif cherryResult == 3:
                result = self.tr("3rd")
            elif cherryResult == 4:
                result = self.tr("4rd")
            elif cherryResult == 5:
                result = self.tr("5rd")
            elif cherryResult == 6:
                result = self.tr("6rd")
            elif cherryResult == 7:
                result = self.tr("7rd")
            else:
                result = self.tr("8th")

        self.gameId = game['gameId']

        self.mapIcon.setPicture(game['mapIcon'])
        self.mapIcon.setFixedSize(54, 54)
        self.resultLabel.setText(result)
        self.infoLabel.setText(
            f"{game['mapName']}  ·  {game['modeName']}  ·  {game['gameDuration']}  ·  {game['gameCreation']}  ·  "
            + self.tr("Game ID: ") + f"{self.gameId}")

        self.copyGameIdButton.setVisible(True)

    def setColor(self, c1: QColor, c2, c3, c4):
        self.setStyleSheet(f"""
            GameTitleBar {{
                background-color: {c1.name(QColor.HexArgb)};
                border-color: {c4.name(QColor.HexArgb)}
            }}
        """)

    def __connectSignalToSlot(self):
        self.copyGameIdButton.clicked.connect(
            lambda: pyperclip.copy(self.gameId))


class GamesView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.stackWidget = QStackedWidget()

        self.loadingPage = QWidget()
        self.infoPage = QWidget()

        self.infoLayout = QHBoxLayout(self.infoPage)
        self.gamesTab = GamesTab()
        self.gameDetailView = GameDetailView()

        self.loadingLayout = QHBoxLayout(self.loadingPage)
        self.processRing = IndeterminateProgressRing()

        self.__initLayout()

    def __initLayout(self):
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.infoLayout.setSpacing(0)

        self.infoLayout.addWidget(self.gamesTab)
        self.infoLayout.addWidget(self.gameDetailView)

        self.loadingLayout.addWidget(self.processRing)
        self.stackWidget.addWidget(self.loadingPage)
        self.stackWidget.addWidget(self.infoPage)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(self.stackWidget)
        self.stackWidget.setCurrentIndex(1)

    def setLoadingPageEnable(self, enable):
        index = 0 if enable else 1
        self.stackWidget.setCurrentIndex(index)


class GameTab(ColorAnimationFrame):
    def __init__(self, game=None, parent=None):
        if game['remake']:
            type = 'remake'
        elif game['win']:
            type = 'win'
        else:
            type = 'lose'

        super().__init__(type=type, parent=parent)

        self.setFixedHeight(54)
        self.setFixedWidth(141)

        self.setProperty("selected", False)

        self.vBoxLayout = QHBoxLayout(self)
        self.nameTimeKdaLayout = QVBoxLayout()

        self.gameId = game["gameId"]
        self.championIcon = RoundIcon(game["championIcon"], 32, 2, 2)

        self.modeName = QLabel(game["name"].replace("排位赛 ", ""))

        self.time = QLabel(
            f"{game['shortTime']}  {game['kills']}/{game['deaths']}/{game['assists']}")
        self.resultLabel = QLabel()

        if game["remake"]:
            self.resultLabel.setText(self.tr("remake"))
        elif game["win"]:
            self.resultLabel.setText(self.tr("win"))
        else:
            self.resultLabel.setText(self.tr("lose"))

        self.remake = game['remake']
        self.win = game['win']

        self.__initWidget()
        self.__initLayout()

        self.clicked.connect(lambda: signalBus.gameTabClicked.emit(self))

    def __initWidget(self):
        self.time.setObjectName("time")

    def __initLayout(self):
        self.nameTimeKdaLayout.addWidget(self.modeName)
        self.nameTimeKdaLayout.addWidget(self.time)

        self.vBoxLayout.addWidget(self.championIcon)
        self.vBoxLayout.addSpacing(2)
        self.vBoxLayout.addLayout(self.nameTimeKdaLayout)

        self.vBoxLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))


class SearchInterface(SeraphineInterface):
    summonerPuuidGetted = pyqtSignal(str)
    gamesNotFound = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.puuid = 0
        self.gameLoadingTask: asyncio.Task = None

        self.vBoxLayout = QVBoxLayout(self)

        self.searchLayout = QHBoxLayout()
        self.searchLineEdit = SearchLineEdit()
        self.careerButton = PushButton(self.tr("Career"))
        self.filterComboBox = ComboBox()

        self.gamesView = GamesView()
        self.currentSummonerName = None

        self.detailViewLoadTask = None
        self.loadingGameId = 0

        self.loadFirstPageLock = asyncio.Lock()

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.searchLineEdit.setAlignment(Qt.AlignCenter)
        self.searchLineEdit.setClearButtonEnabled(True)
        self.searchLineEdit.setPlaceholderText(
            self.tr("Please input summoner name"))
        self.careerButton.setEnabled(False)
        self.filterComboBox.setEnabled(False)

        StyleSheet.SEARCH_INTERFACE.apply(self)

        self.filterComboBox.addItems([
            self.tr('All'),
            self.tr('Normal'),
            self.tr("A.R.A.M."),
            self.tr("Ranked Solo"),
            self.tr("Ranked Flex")
        ])
        self.filterComboBox.setCurrentIndex(0)

    def __initLayout(self):
        self.searchLayout.addWidget(self.searchLineEdit)
        self.searchLayout.addSpacing(5)
        self.searchLayout.addWidget(self.careerButton)
        self.searchLayout.addWidget(self.filterComboBox)

        self.vBoxLayout.addLayout(self.searchLayout)
        self.vBoxLayout.addSpacing(5)
        self.vBoxLayout.addWidget(self.gamesView)
        self.vBoxLayout.setContentsMargins(30, 32, 30, 30)

    def setEnabled(self, a0: bool) -> None:
        self.gamesView.gameDetailView.clear()
        self.gamesView.gamesTab.clear()
        self.searchLineEdit.clear()

        self.searchLineEdit.setEnabled(a0)
        self.searchLineEdit.searchButton.setEnabled(a0)

        if not a0:
            self.filterComboBox.setEnabled(a0)

        return super().setEnabled(a0)

    def __connectSignalToSlot(self):
        self.searchLineEdit.searchButton.setShortcut(Qt.Key.Key_Return)
        self.searchLineEdit.searchButton.clicked.connect(
            self.onSearchButtonClicked)

        self.careerButton.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(self.puuid))
        self.filterComboBox.currentIndexChanged.connect(
            self.__onFilterComboxChanged)

        signalBus.gameTabClicked.connect(self.__onGameTabClicked)

    def __showSummonerNotFoundMsg(self):
        InfoBar.error(
            title=self.tr("Summoner not found"),
            content=self.tr("Please check the summoner's name and retry"),
            orient=Qt.Vertical,
            duration=5000,
            position=InfoBarPosition.BOTTOM_RIGHT,
            parent=self
        )

    # Fix: 超快速的在候选栏选中两次同样puuid会起两个task加载战绩, 100%干掉客户端 -- By Hpero4
    @asyncLockDecorator('loadFirstPageLock')
    async def searchAndShowFirstPage(self, puuid=None):
        name = self.searchLineEdit.text()
        if name == "":
            return False

        if re.match(r"\S+-\S+-\S+-\S+-\S+", name):
            summoner = await connector.getSummonerByPuuid(name)
        else:
            summoner = await connector.getSummonerByName(name)

        if 'errorCode' in summoner:
            self.__showSummonerNotFoundMsg()
            return False

        self.gamesView.setLoadingPageEnable(True)

        self.careerButton.setEnabled(True)
        self.filterComboBox.setEnabled(True)

        self.gamesView.gameDetailView.clear()

        # NOTE 如果是生涯和搜索反复横跳, 就不重新启 loadgames 任务了
        if puuid != self.puuid:
            if self.gameLoadingTask:
                self.gameLoadingTask.cancel()

                while not self.gameLoadingTask.cancelled() \
                        and not self.gameLoadingTask.done():
                    await asyncio.sleep(.3)

            self.puuid = summoner['puuid']
            self.gamesView.gamesTab.clear()

            # 先加载两页，让用户看着
            try:
                games = await connector.getSummonerGamesByPuuid(self.puuid, 0, 19)
            except SummonerGamesNotFound:
                games = []
            else:
                games = await parseGamesDataConcurrently(games['games'])

            if len(games) == 0:
                self.gamesView.gamesTab.nextButton.setVisible(False)
                self.gamesView.gamesTab.prevButton.setVisible(False)
                self.filterComboBox.setEnabled(False)
                self.gamesView.setLoadingPageEnable(False)
                return False

            self.gamesView.gamesTab.updateQueueIdMap(games)

            # 启动任务，往 gamesTab 里丢数据
            # NOTE 既然创建新任务, 并且刷新了self.puuid 就应该用self的, 否则就违背了loadGames判断的初衷

            self.gameLoadingTask = asyncio.create_task(
                self.__loadGames(self.puuid))

        self.gamesView.gamesTab.showTheFirstPage()
        self.gamesView.setLoadingPageEnable(False)

        self.__addSearchHistroy(name)

        return True

    def waitingForDrawSelect(self, gameId):
        '''
        从生涯界面点下面进来的时候，绘制选中的提示框
        '''
        tabs = self.gamesView.gamesTab

        # 遍历一下目前已经画好的第一页有没有这个 tab，有的话就直接画上
        # FIXME -- By Hpero4
        #  如果在绘制前Tabs的StackWidget改变, 会导致画错框甚至找不到绘制对象 AttributeError
        #  必须保证绘制时界面没有被改变; (目前暂时是将耗时操作移到画框后面)
        # FIXME -- By Hpero4
        #  如果选中的对局不在第一页中(> 11), 将不会画上选择框
        layout = tabs.stackWidget.widget(1).layout()
        for i in range(layout.count()):
            item = layout.itemAt(i)

            if not item.widget():
                continue

            widget: GameTab = item.widget()

            if widget.gameId == int(gameId):
                tabs.currentTabSelected = widget
                widget.setProperty("selected", True)
                widget.style().polish(widget)
                widget.repaint()
            else:
                widget.setProperty("selected", False)
                widget.style().polish(widget)

        if not tabs.currentTabSelected:
            tabs.waitingForSelect = gameId

    @asyncSlot()
    async def onSearchButtonClicked(self):
        if not await self.searchAndShowFirstPage():
            return

        self.filterComboBox.setCurrentIndex(0)  # 将筛选条件回调至"全部" -- By Hpero4
        self.gamesView.gamesTab.clickFirstTab()

    async def __loadGames(self, puuid):
        begIdx = 20
        endIdx = 29

        logger.debug(f"welcome load games task: {puuid}", TAG)

        # NOTE 换了查询目标, 若之前正在查, 先等 task 被 release 掉 -- By Hpero4
        while self.gameLoadingTask \
                and not self.gameLoadingTask.done() \
                and puuid != self.puuid:
            await asyncio.sleep(.2)

        logger.debug(f"start load {puuid}", TAG)
        # 连续查多个人时, 将前面正在查的task给release掉
        while self.puuid == puuid:
            # 为加载战绩详情让行
            while (
                (self.detailViewLoadTask and not self.detailViewLoadTask.done())
                or
                (self.window().careerInterface.loadGamesTask and not self.window(
                ).careerInterface.loadGamesTask.done())
            ):
                await asyncio.sleep(.2)

            t1 = time.time()
            try:
                games = await connector.getSummonerGamesByPuuidSlowly(
                    puuid, begIdx, endIdx)
            except SummonerGamesNotFound:
                # TODO 这里可以弹个窗  -- By Zzaphkiel
                # NOTE 触发 SummonerGamesNotFound 时, 异常信息会通过 connector 下发到 main_window 的 __onShowLcuConnectError
                #  理论上会有弹框提示  -- By Hpero4
                return
            t2 = time.time()

            logger.debug(f"load games {self.puuid} [{begIdx}-{endIdx}] finish {t2-t1}s", TAG)
            # 1000 局搜完了，或者正好上一次就是最后
            # 在切换了puuid时, 就不要再把数据刷到Games上了 -- By Hpero4
            if games['gameCount'] == 0 or self.puuid != puuid:
                return

            # 处理数据，交给 gamesTab，更新其 games 成员以及 queueIdMap
            games = await parseGamesDataConcurrently(games['games'])

            if self.puuid != puuid:
                return

            self.gamesView.gamesTab.updateQueueIdMap(games)

            # 如果用户下一页点得太猛，在还没加载完的时候点到了能绘制的最后一页
            # 由于 __onNextButtonClicked 的逻辑，在用户进入最后一页时，nextButton 会被设置为不可用。
            # 而现在，由于新的两页对局数据加载好了，可以绘制上去了，要让 button 变得可用
            self.gamesView.gamesTab.resetButtonEnabled()

            # 如果长度小于 10，也说明搜完了已经
            if len(games) < 10:
                return

            begIdx = endIdx + 1
            endIdx = begIdx + 9

            # 睡不睡都行
            await asyncio.sleep(.1)

    @asyncSlot(QWidget)
    async def __onGameTabClicked(self, tab: GameTab):
        tabs: GamesTab = self.gamesView.gamesTab
        cur: GameTab = tabs.currentTabSelected

        if tab is cur:
            return

        self.gamesView.gameDetailView.clear()

        tab.setProperty("selected", True)
        tab.style().polish(tab)

        if cur:
            cur.setProperty("selected", False)
            cur.style().polish(cur)

        tabs.currentTabSelected = tab
        self.loadingGameId = tab.gameId
        await self.updateGameDetailView(tab.gameId, self.puuid)

    async def updateGameDetailView(self, gameId, puuid):
        if cfg.get(cfg.showTierInGameInfo):
            self.gamesView.gameDetailView.setLoadingPageEnabled(True)

        # NOTE self.detailViewLoadTask 用于标记详情正在加载, self.loadGame会为其让行 -- By Hpero4
        self.detailViewLoadTask = asyncio.create_task(
            connector.getGameDetailByGameId(gameId))
        game = await self.detailViewLoadTask

        # 加载GameDetail的过程中, 切换了搜索对象(self.puuid变更), 将后续任务pass -- By Hpero4
        if puuid == self.puuid:
            self.detailViewLoadTask = asyncio.create_task(
                parseGameDetailData(puuid, game))
            game = await self.detailViewLoadTask
            self.gamesView.gameDetailView.updateGame(game)

        if cfg.get(cfg.showTierInGameInfo):
            self.gamesView.gameDetailView.setLoadingPageEnabled(False)

    @asyncSlot(int)
    async def __onFilterComboxChanged(self, index):
        self.gamesView.gameDetailView.clear()
        tabs = self.gamesView.gamesTab
        tabs.clearTabs()

        ids = (-1, 430, 450, 420, 440)
        tabs.queueId = ids[index]

        self.gamesView.setLoadingPageEnable(True)

        while len(self.gamesView.gamesTab.queueIdMap.get(tabs.queueId, [])) < 10:
            await asyncio.sleep(.2)

        enable = tabs.queueIdMap.get(tabs.queueId) is not None
        tabs.prevButton.setVisible(enable)
        tabs.nextButton.setVisible(enable)
        tabs.nextButton.setVisible(enable)

        self.gamesView.setLoadingPageEnable(False)

        if enable:
            self.gamesView.gamesTab.showTheFirstPage()

    def __addSearchHistroy(self, name):
        history: list = cfg.get(cfg.searchHistory).split(',')

        if name in history:
            history.remove(name)

        history.insert(0, name)
        cfg.set(cfg.searchHistory, ",".join(
            [t for t in history if t][:10]), True)
