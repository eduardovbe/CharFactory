"""Componentes da interface: painel lateral com cabeçalho, seções recolhíveis, navegador
por slider, editor de eixo com nudge de 1 px, faixa de ferramentas e timeline de ticks.

Medidas comuns (para os painéis ficarem alinhados entre si): rótulos de campo com LABEL_W de
largura mínima, margens de PANEL_MARGINS e GAP entre linhas."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QScrollArea, QTabBar,
                               QSizePolicy, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from .icons import icon
from ..i18n import tr

LABEL_W = 64                       # largura mínima dos rótulos de campo
GAP = 6                            # espaço entre linhas de um painel
PANEL_MARGINS = (10, 8, 10, 10)     # esquerda, topo, direita, base do conteúdo dos painéis


class PanelHeader(QLabel):
    """Rótulo de seção discreto em caixa-alta, em estilo minimalista."""

    def __init__(self, text: str):
        super().__init__(text.upper())
        self.setObjectName("panelHeader")
        f = self.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.85))
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self.setFont(f)


class PanelScroll(QScrollArea):
    """Área rolável dos painéis que mede a altura do conteúdo na largura real do viewport.
    O QScrollArea calcula heightForWidth sem descontar a barra de rolagem; com textos que
    quebram linha, o conteúdo ficava mais baixo que o necessário e uma seção era espremida."""

    def setWidget(self, w):  # noqa: N802
        super().setWidget(w)
        w.installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.widget() and e.type() == QEvent.Type.LayoutRequest:
            self._fit()
        return super().eventFilter(obj, e)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        w = self.widget()
        lay = w.layout() if w is not None else None
        if lay is None:
            return
        width = self.viewport().width()
        h = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalMinimumSize().height()
        h = max(h, lay.totalMinimumSize().height())
        if h > 0 and w.minimumHeight() != h:
            w.setMinimumHeight(h)


class SidePanel(QWidget):
    """Cabeçalho + conteúdo rolável, com as margens e o espaçamento padrão dos painéis."""

    def __init__(self, title: str, content: QWidget, min_width: int = 260, flush: bool = False,
                 header: bool = True):
        super().__init__()
        self.header = PanelHeader(title)
        scroll = PanelScroll()
        scroll.setWidgetResizable(True)
        # com tamanho já dado, o setWidget não mede o painel inteiro (sizeHint, que aplica o QSS
        # em todos os filhos) agora; o widgetResizable ajusta o tamanho quando o painel aparece
        content.resize(min_width, 1)
        scroll.setWidget(content)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # o conteúdo se ajusta à largura do painel em vez de vazar para os lados
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        self.setObjectName("sidePanel")
        content.setObjectName("panelBody")
        scroll.viewport().setObjectName("panelBody")
        lay_c = content.layout()
        if lay_c is not None:
            # 'flush' (listas e árvores) usa as mesmas margens laterais dos outros painéis: o
            # conteúdo não pula de lugar ao trocar de editor; só o topo e a base são mais justos
            lay_c.setContentsMargins(*((PANEL_MARGINS[0], 10, PANEL_MARGINS[2], 10) if flush else PANEL_MARGINS))
            lay_c.setSpacing(GAP)
        # checkbox não quebra linha: com texto longo ele alargava o painel inteiro e cortava os
        # controles da direita; assim só o próprio texto é cortado (completo na dica)
        for cb in content.findChildren(QCheckBox):
            cb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            if not cb.toolTip():
                cb.setToolTip(cb.text())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(scroll, 1)
        # o painel esquerdo da janela principal não leva cabeçalho: o editor ativo já está
        # marcado na barra de editores. (Só depois de ter pai: sem pai o Qt o trata como janela.)
        if not header:
            self.header.hide()
        self.setMinimumWidth(min_width)


FILE_ICONS = {".def": "definitions", ".sff": "sprites", ".air": "anims", ".cmd": "commands",
              ".cns": "states", ".st": "states", ".snd": "sounds", ".act": "palette"}


def file_icon(name: str):
    """Ícone do editor correspondente à extensão do arquivo."""
    import os
    return icon(FILE_ICONS.get(os.path.splitext(name)[1].lower(), "definitions"))


class DocStrip(QWidget):
    """Faixa de abas de documento acima de um editor (ex.: 'kfm.sff ×').

    A aba ativa se funde com a área de trabalho logo abaixo; a faixa ocupa a largura toda e
    aceita widgets à direita (add_trailing), como o tamanho do sprite ou o zoom."""

    def __init__(self, bar=None):
        super().__init__()
        from PySide6.QtWidgets import QTabBar
        self.setObjectName("docStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bar = bar or QTabBar()
        self.bar.setObjectName("docTabs")
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setIconSize(QSize(15, 15))
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 0, 10, 0)
        self.lay.setSpacing(8)
        self.lay.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignBottom)
        self.lay.addStretch(1)

    def add_trailing(self, w: QWidget) -> QWidget:
        self.lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def set_file(self, name: str, icon_name: str | None = None) -> None:
        """Modo de documento único: uma aba com o nome e o ícone do arquivo (ou o ícone dado).
        Com um documento só a faixa não diz nada que a barra de editores já não diga: fica
        escondida."""
        if not self.bar.count():
            self.bar.addTab("")
        self.bar.setTabText(0, name)
        self.bar.setTabIcon(0, icon(icon_name) if icon_name else file_icon(name))
        self.setVisible(False)


class ElidedCombo(QComboBox):
    """QComboBox que corta o texto longo com '…' em vez de decepar no meio da palavra."""

    def paintEvent(self, e):  # noqa: N802
        from PySide6.QtWidgets import QStyle, QStyleOptionComboBox, QStylePainter
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        r = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                        QStyle.SubControl.SC_ComboBoxEditField, self)
        room = r.width() - (self.iconSize().width() + 6 if not opt.currentIcon.isNull() else 0) - 4
        opt.currentText = self.fontMetrics().elidedText(opt.currentText, Qt.TextElideMode.ElideRight, max(room, 10))
        p.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)


class ChipTabBar(QTabBar):
    """Barra de abas em chip com botão de fechar próprio. O botão padrão do Qt fica colado na
    borda direita da aba (por cima do contorno) e o QSS não consegue afastá-lo; aqui ele vem
    dentro de uma caixa com respiro dos dois lados."""

    def tabInserted(self, index):  # noqa: N802
        super().tabInserted(index)
        box = QWidget()
        box.setObjectName("plain")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 0, 6, 0)
        lay.setSpacing(0)
        b = QToolButton()
        b.setObjectName("tabClose")
        b.setIcon(icon("close"))
        b.setIconSize(QSize(12, 12))
        b.setFixedSize(24, 24)          # alvo de clique de 24 px; o × continua pequeno
        b.setAutoRaise(True)
        b.setToolTip(tr("Close this project"))
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self._close(box))
        lay.addWidget(b)
        self.setTabButton(index, QTabBar.ButtonPosition.RightSide, box)

    def _close(self, box) -> None:
        for i in range(self.count()):
            if self.tabButton(i, QTabBar.ButtonPosition.RightSide) is box:
                self.tabCloseRequested.emit(i)
                return


class SegmentedTabs(QWidget):
    """Páginas trocadas por um controle segmentado (em vez de abas soltas).

    Tem a API usada das QTabWidget: addTab, setCurrentIndex, currentIndex, setTabText."""
    currentChanged = Signal(int)

    def __init__(self):
        super().__init__()
        from PySide6.QtWidgets import QButtonGroup, QStackedWidget
        self.stack = QStackedWidget()
        self.bar = QFrame()
        self.bar.setObjectName("segmented")
        self.bar_lay = QHBoxLayout(self.bar)
        self.bar_lay.setContentsMargins(3, 3, 3, 3)
        self.bar_lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.idClicked.connect(self.setCurrentIndex)
        foot = QWidget()
        foot.setObjectName("segmentedFoot")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 8, 10, 10)
        fl.addWidget(self.bar)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.stack, 1)
        lay.addWidget(foot)

    def addTab(self, widget: QWidget, text: str, icon_name: str | None = None) -> int:  # noqa: N802
        i = self.stack.addWidget(widget)
        b = QToolButton()
        b.setObjectName("segment")
        b.setText(text)
        if icon_name:
            b.setIcon(icon(icon_name))
            b.setIconSize(QSize(15, 15))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setCheckable(True)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.group.addButton(b, i)
        self.bar_lay.addWidget(b)
        if i == 0:
            b.setChecked(True)
        return i

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        self.stack.setCurrentIndex(i)
        self.group.button(i).setChecked(True)
        self.currentChanged.emit(i)

    def currentIndex(self) -> int:  # noqa: N802
        return self.stack.currentIndex()

    def setTabText(self, i: int, text: str) -> None:  # noqa: N802
        self.group.button(i).setText(text)


# ======================================================================= roda do mouse

class WheelGuard(QObject):
    """A roda do mouse só muda campos numéricos, listas suspensas, sliders e abas que o usuário
    clicou antes (com foco) e numa janela ativa. Com o ponteiro só passando por cima, a roda
    rola o painel em volta (ou não faz nada fora de área rolável).

    O Qt dá foco pela roda (Qt.WheelFocus, padrão do spinbox) antes de qualquer filtro ver o
    evento; por isso esses campos passam a StrongFocus quando são polidos."""

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t == QEvent.Type.Polish:
            from PySide6.QtWidgets import QAbstractSpinBox, QComboBox
            if isinstance(obj, (QAbstractSpinBox, QComboBox, QSlider)) and \
                    obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return False
        if t != QEvent.Type.Wheel:
            return False
        from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QTabBar
        if not isinstance(obj, QWidget):
            return False
        # a roda chega primeiro na caixa de texto interna do spinbox/combo editável
        parent = obj.parentWidget()
        if isinstance(parent, (QAbstractSpinBox, QComboBox)) and not isinstance(obj, (QAbstractSpinBox, QComboBox)):
            if getattr(parent, "lineEdit", lambda: None)() is obj:
                obj = parent
        if not isinstance(obj, (QAbstractSpinBox, QComboBox, QSlider, QTabBar)):
            return False
        if obj.hasFocus() and obj.window().isActiveWindow() and not isinstance(obj, QTabBar):
            return False
        w = obj.parentWidget()
        while w is not None and not isinstance(w, QAbstractScrollArea):
            w = w.parentWidget()
        if w is not None:
            bar = w.horizontalScrollBar() if e.angleDelta().x() and not e.angleDelta().y() else w.verticalScrollBar()
            QApplication.sendEvent(bar, e)
        return True             # fora de área rolável a roda não faz nada no campo


def install_wheel_guard(app) -> None:
    if getattr(app, "_charfactory_wheel_guard", None) is None:
        app._charfactory_wheel_guard = WheelGuard(app)
        app.installEventFilter(app._charfactory_wheel_guard)
        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox
        for w in app.allWidgets():      # os que já foram polidos antes do filtro existir
            if isinstance(w, (QAbstractSpinBox, QComboBox, QSlider)) and w.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class PolishFixes(QObject):
    """Acabamento aplicado a todo widget quando o Qt o prepara (Polish), para não depender de
    cada diálogo lembrar:
    - o botão de confirmar de um QDialogButtonBox (OK/Salvar/Sim/Aplicar) é o primário; antes
      o 'botão padrão' do Enter ficava destacado, que muitas vezes era 'Copiar' ou 'Fechar';
    - spinboxes com o mesmo recuo de texto das caixas de texto (o QSS não chega no QLineEdit
      interno e o número ficava colado na borda)."""

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.Polish:
            return False
        from PySide6.QtWidgets import QAbstractSpinBox, QDialogButtonBox, QPushButton
        if isinstance(obj, QPushButton) and obj.property("primary") is None:
            box = obj.parentWidget()
            if isinstance(box, QDialogButtonBox) and box.buttonRole(obj) in (
                    QDialogButtonBox.ButtonRole.AcceptRole, QDialogButtonBox.ButtonRole.YesRole,
                    QDialogButtonBox.ButtonRole.ApplyRole):
                obj.setProperty("primary", True)
                obj.style().unpolish(obj)
                obj.style().polish(obj)
        elif isinstance(obj, QAbstractSpinBox) and obj.lineEdit() is not None:
            obj.lineEdit().setTextMargins(3, 0, 0, 0)
        return False


def install_ui_polish(app) -> None:
    if getattr(app, "_charfactory_polish", None) is None:
        app._charfactory_polish = PolishFixes(app)
        app.installEventFilter(app._charfactory_polish)


def strong_focus(*widgets: QWidget) -> None:
    """Sem foco pela roda: o campo só ganha foco com clique ou Tab."""
    for w in widgets:
        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


# ======================================================================= layout em fluxo

class FlowLayout(QLayout):
    """Itens lado a lado que quebram linha quando a largura acaba (como texto)."""

    def __init__(self, parent=None, spacing: int = 2):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        # a altura é fixada pelo dono (ToolStrip.resizeEvent): heightForWidth dentro de uma
        # QScrollArea fazia o painel errar a altura e espremer as outras seções
        return False

    def heightForWidth(self, w):  # noqa: N802
        return self._place(QRect(0, 0, w, 0), dry=True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self):  # noqa: N802
        # largura de tudo numa linha: o Qt calcula a altura preferida com heightForWidth dessa
        # largura; com a largura mínima (um botão) a faixa pedia um botão por linha de altura
        w = h = 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            w += hint.width() + self._spacing
            h = max(h, hint.height())
        m = self.contentsMargins()
        return QSize(max(0, w - self._spacing) + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, dry: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        items = [it for it in self._items if it.widget() is None or not it.widget().isHidden()]
        for k, it in enumerate(items):
            hint = it.sizeHint()
            sep = it.widget() is not None and it.widget().property("flowSeparator")
            if sep:
                # separador no começo ou no fim de uma linha não separa nada: some
                nxt = items[k + 1].sizeHint().width() if k + 1 < len(items) else 0
                if x == r.x() or x + hint.width() + self._spacing + nxt > r.right() + 1:
                    if not dry:
                        it.setGeometry(QRect(QPoint(x, y), QSize(0, 0)))
                    continue
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x = r.x()
                y += line_h + self._spacing
                line_h = 0
            if not dry:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


_TIP_SHORTCUT = re.compile(r"\(((?:Ctrl|Alt|Shift)\+[^)]+|F\d+|Ins)\)")


def tip_shortcut(tip: str) -> QKeySequence | None:
    """Atalho escrito na dica, como em 'Duplicar (Ctrl+D)'. Ele só vale com o botão visível:
    o Qt ignora atalhos de widgets escondidos, então cada editor usa os seus."""
    m = _TIP_SHORTCUT.search(tip)
    return QKeySequence(m[1]) if m else None


MORE = "more"      # em tool_bar: o que vem depois vai para o menu "⋯" (com nome e atalho)


class ToolStrip(QWidget):
    """Faixa de botões de ferramenta que quebra linha num painel estreito. A QToolBar
    escondia o que não cabia atrás de um '»' quase invisível; aqui os botões principais ficam
    à vista e o resto vai, com nome, para o menu '⋯' do fim da faixa."""

    def __init__(self, size: int = 18):
        super().__init__()
        self.setObjectName("toolStrip")
        self.icon_px = size      # não "size": esconderia QWidget.size()
        self.flow = FlowLayout(self, 2)
        self.actions_by_tip: dict[str, QAction] = {}
        self.more_menu = None
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, e):  # noqa: N802 — quantas linhas cabem nesta largura
        super().resizeEvent(e)
        h = self.flow.heightForWidth(self.width())
        if h > 0 and h != self.height():
            self.setFixedHeight(h)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if self.width() > 0:
            h = self.flow.heightForWidth(self.width())
            if h > 0:
                self.setFixedHeight(h)

    def add(self, name: str, tip: str, fn, checkable: bool = False, in_menu: bool = False) -> QAction:
        a = QAction(icon(name), tip, self)
        a.setToolTip(tip)
        seq = tip_shortcut(tip)
        if seq:
            a.setShortcut(seq)
            # no menu o atalho já aparece à direita: o texto fica só com o nome
            a.setText(_TIP_SHORTCUT.sub("", tip).replace(" · ", " ").strip())
        if checkable:
            a.setCheckable(True)
            a.toggled.connect(fn)
        else:
            a.triggered.connect(fn)
        self.addAction(a)       # o atalho vale com a ação no menu ou no botão
        if in_menu:
            self._menu().addAction(a)
        else:
            b = QToolButton(self)
            b.setDefaultAction(a)
            b.setIconSize(QSize(self.icon_px, self.icon_px))
            b.setAutoRaise(True)
            self.flow.addWidget(b)
        self.actions_by_tip[tip] = a
        return a

    def _menu(self):
        if self.more_menu is None:
            from PySide6.QtWidgets import QMenu
            self.more_menu = QMenu(self)
            b = QToolButton(self)
            b.setObjectName("moreButton")
            b.setIcon(icon("overflow"))
            b.setIconSize(QSize(self.icon_px, self.icon_px))
            b.setToolTip(tr("More tools"))
            b.setAutoRaise(True)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            b.setMenu(self.more_menu)
            self.flow.addWidget(b)
        return self.more_menu

    def add_separator(self, in_menu: bool = False) -> None:
        if in_menu:
            self._menu().addSeparator()
            return
        box = QWidget(self)
        box.setObjectName("plain")
        box.setProperty("flowSeparator", True)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(4, 5, 4, 5)
        line = QFrame()
        line.setObjectName("toolSep")
        line.setFixedSize(1, self.icon_px + 4)
        bl.addWidget(line)
        self.flow.addWidget(box)


def tool_bar(items: list, size: int = 18) -> ToolStrip:
    """items: (ícone, dica, função[, checkable]), None para separador ou MORE (o que vem
    depois vai para o menu '⋯'). O atalho entre parênteses na dica é ligado à ação."""
    tb = ToolStrip(size)
    in_menu = False
    for it in items:
        if it == MORE:
            in_menu = True
        elif it is None:
            tb.add_separator(in_menu)
        else:
            name, tip, fn, *rest = it
            tb.add(name, tip, fn, bool(rest and rest[0]), in_menu)
    return tb


def tool_button(name: str, tip: str, fn, checkable: bool = False, size: int = 18) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(name))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    seq = tip_shortcut(tip)
    if seq:
        b.setShortcut(seq)
    b.setAutoRaise(True)
    b.setCheckable(checkable)
    (b.toggled if checkable else b.clicked).connect(fn)
    return b


# ======================================================================= formulários

def field_label(text: str, width: int = LABEL_W) -> QLabel:
    """Rótulo de campo com largura mínima comum: os campos dos painéis ficam numa coluna só."""
    lbl = QLabel(text)
    lbl.setObjectName("fieldLabel")
    lbl.setMinimumWidth(width)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def form_row(label: str | None, *items, width: int = LABEL_W) -> QHBoxLayout:
    """Linha 'Rótulo  [campo] [botão]…'. Os campos esticam por igual; inteiros viram espaço fixo."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    if label is not None:
        row.addWidget(field_label(label, width))
    for it in items:
        if isinstance(it, int):
            row.addSpacing(it)          # reserva o lugar de um botão, para alinhar com as outras linhas
        elif isinstance(it, QLayout):
            row.addLayout(it, 1)
        else:
            # campos dividem a largura igualmente; botões e rótulos ficam do tamanho deles
            row.addWidget(it, 0 if isinstance(it, (QToolButton, QLabel)) else 1)
    return row


def filter_box(placeholder: str, on_text) -> "QLineEdit":
    """Campo de filtro com lupa e botão de limpar; chama on_text(texto) a cada tecla."""
    from PySide6.QtWidgets import QLineEdit
    e = QLineEdit()
    e.setObjectName("filter")
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(True)
    e.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
    e.textChanged.connect(on_text)
    return e


def hint_label(text: str) -> QLabel:
    """Texto de ajuda discreto, com quebra de linha."""
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


class Section(QWidget):
    """Grupo do painel com cabeçalho clicável que recolhe o conteúdo. O estado (aberto ou
    fechado) é lembrado entre sessões quando a seção tem uma chave."""
    store = None        # dicionário de configurações (o MainWindow liga ao ctx.settings)
    toggled = Signal(bool)

    def __init__(self, title: str, key: str | None = None, collapsed: bool = False):
        super().__init__()
        self.key = key
        self.setObjectName("section")
        self.head = QToolButton()
        self.head.setObjectName("sectionHeader")
        self.head.setText(title)
        self.head.setCheckable(True)
        self.head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.head.setIconSize(QSize(12, 12))
        self.head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head_row = QHBoxLayout()
        self.head_row.setContentsMargins(0, 0, 0, 0)
        self.head_row.setSpacing(4)
        self.head_row.addWidget(self.head, 1)
        line = QFrame()
        line.setObjectName("sectionRule")
        line.setFixedHeight(1)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 2, 0, 4)
        self.body_lay.setSpacing(GAP - 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(line)
        lay.addLayout(self.head_row)
        lay.addWidget(self.body)
        is_open = not collapsed
        if key and self.store is not None:
            is_open = self.store.get("sections", {}).get(key, is_open)
        self.head.setChecked(is_open)
        self._apply(is_open)
        self.head.toggled.connect(self._toggle)

    def add(self, *items) -> "Section":
        for it in items:
            if isinstance(it, QLayout):
                self.body_lay.addLayout(it)
            else:
                self.body_lay.addWidget(it)
        return self

    def add_header_widget(self, w: QWidget) -> QWidget:
        self.head_row.addWidget(w)
        return w

    def is_open(self) -> bool:
        return self.head.isChecked()

    def _apply(self, on: bool) -> None:
        self.body.setVisible(on)
        self.head.setIcon(icon("chevron_down" if on else "chevron_right"))

    def _toggle(self, on: bool) -> None:
        self._apply(on)
        if self.key and self.store is not None:
            self.store.setdefault("sections", {})[self.key] = on
        self.toggled.emit(on)


class Navigator(QWidget):
    """Slider entre setas ◀ ▶ (passo de 1, repetem se seguradas) e o contador 'n / total'."""
    indexChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("plain")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._changed)
        self.prev = tool_button("left", tr("Previous"), lambda: self.step(-1))
        self.next = tool_button("right", tr("Next"), lambda: self.step(1))
        for b in (self.prev, self.next):
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(60)
            b.setIconSize(QSize(14, 14))
        self.label = QLabel("0 / 0")
        self.label.setObjectName("counter")
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label.setMinimumWidth(58)
        self.suffix = ""
        self.count = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.prev)
        row.addWidget(self.slider, 1)
        row.addWidget(self.next)
        row.addWidget(self.label)

    def step(self, d: int) -> None:
        self.slider.setValue(self.slider.value() + d)

    def set_count(self, n: int) -> None:
        self.count = n
        self.slider.blockSignals(True)
        self.slider.setMaximum(max(0, n - 1))
        self.slider.blockSignals(False)
        self._update()

    def set_index(self, i: int) -> None:
        self.slider.setValue(i)
        self._update()

    def index(self) -> int:
        return self.slider.value()

    def set_suffix(self, s: str) -> None:
        self.suffix = s
        self._update()

    def _changed(self, v: int) -> None:
        self._update()
        self.indexChanged.emit(v)

    def _update(self) -> None:
        v = self.slider.value()
        cur = v + 1 if self.count else 0
        self.label.setText(f"{cur} / {self.count}")
        self.label.setToolTip(f"{cur} de {self.count}{self.suffix}")
        self.prev.setEnabled(v > 0)
        self.next.setEnabled(v < self.slider.maximum())


def compact(*widgets: QWidget, min_width: int = 58) -> None:
    """Campos que encolhem com o painel lateral. Sem isso o Qt usa a largura que cabe o maior
    valor possível (99999999, -360.00…) e o conteúdo estoura o painel."""
    for w in widgets:
        w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        w.setMinimumWidth(min_width)


def spin(lo: int = -99999, hi: int = 99999) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setKeyboardTracking(False)
    compact(s)
    return s


def _small_button(name: str, tip: str, fn) -> QToolButton:
    b = tool_button(name, tip, fn, size=14)
    b.setObjectName("nudge")
    b.setFixedSize(24, 24)
    b.setAutoRepeat(True)
    b.setAutoRepeatDelay(350)
    b.setAutoRepeatInterval(60)
    return b


class AxisEditor(QWidget):
    """Campos X/Y com uma cruz de botões de 1 px (←↑→↓) ao lado."""
    changed = Signal(int, int)

    def __init__(self, xlabel: str = tr("Axis X"), ylabel: str = tr("Axis Y")):
        super().__init__()
        self.setObjectName("plain")
        self.x, self.y = spin(), spin()
        self.x.valueChanged.connect(self._emit)
        self.y.valueChanged.connect(self._emit)
        fields = QVBoxLayout()
        fields.setSpacing(GAP - 2)
        fields.addLayout(form_row(xlabel.rstrip(":"), self.x))
        fields.addLayout(form_row(ylabel.rstrip(":"), self.y))
        pad = QGridLayout()
        pad.setSpacing(1)
        pad.addWidget(_small_button("up", tr("Move 1 px up"), lambda: self.nudge(0, -1)), 0, 1)
        pad.addWidget(_small_button("left", tr("Move 1 px left"), lambda: self.nudge(-1, 0)), 1, 0)
        pad.addWidget(_small_button("right", tr("Move 1 px right"), lambda: self.nudge(1, 0)), 1, 2)
        pad.addWidget(_small_button("down", tr("Move 1 px down"), lambda: self.nudge(0, 1)), 2, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addLayout(fields, 1)
        lay.addLayout(pad)
        self.trailing = lay      # o editor de animações põe 'aplicar aos próximos' aqui
        self._quiet = False

    def set(self, x: int, y: int) -> None:
        self._quiet = True
        self.x.setValue(x)
        self.y.setValue(y)
        self._quiet = False

    def nudge(self, dx: int, dy: int) -> None:
        self._quiet = True
        self.x.setValue(self.x.value() + dx)
        self.y.setValue(self.y.value() + dy)
        self._quiet = False
        self._emit()

    def _emit(self, *_):
        if not self._quiet:
            self.changed.emit(self.x.value(), self.y.value())


class GroupIndex(QWidget):
    """Grupo (com grupo anterior/próximo) e Índice, um por linha e alinhados aos outros campos."""
    changed = Signal(int, int)
    prevGroup = Signal()
    nextGroup = Signal()

    def __init__(self, a: str = tr("Group"), b: str = tr("Index"), group_buttons: bool = True):
        super().__init__()
        self.setObjectName("plain")
        self.g, self.i = spin(-1, 99999), spin(-1, 99999)
        self.g.valueChanged.connect(self._emit)
        self.i.valueChanged.connect(self._emit)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(GAP - 2)
        grid.addWidget(field_label(a.rstrip(":")), 0, 0)
        grid.addWidget(self.g, 0, 1)
        grid.addWidget(field_label(b.rstrip(":")), 1, 0)
        grid.addWidget(self.i, 1, 1)
        grid.setColumnStretch(1, 1)
        if group_buttons:
            grid.addWidget(tool_button("left", tr("Previous group"), self.prevGroup.emit, size=14), 0, 2)
            grid.addWidget(tool_button("right", tr("Next group"), self.nextGroup.emit, size=14), 0, 3)
        self._quiet = False

    def set(self, g: int, i: int) -> None:
        self._quiet = True
        self.g.setValue(g)
        self.i.setValue(i)
        self._quiet = False

    def _emit(self, *_):
        if not self._quiet:
            self.changed.emit(self.g.value(), self.i.value())


class Timeline(QWidget):
    """Régua de ticks com os frames da animação, o frame atual e o cursor de reprodução."""
    tickClicked = Signal(int)
    LEFT = 132

    def __init__(self):
        super().__init__()
        self.durations: list[int] = []
        self.current_tick = 0
        self.current_frame = -1
        self.title = ""
        self.loopstart = 0
        self.theme = None
        self.setFixedHeight(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_frames(self, durations: list[int], loopstart: int, title: str) -> None:
        self.durations = [max(1, d) if d >= 0 else 60 for d in durations]
        self.loopstart = loopstart
        self.title = title
        self.update()

    def set_current_frame(self, i: int) -> None:
        if i != self.current_frame:
            self.current_frame = i
            self.update()

    def frame_start(self, i: int) -> int:
        return sum(self.durations[:i])

    def _ui(self, key: str, fallback: str) -> QColor:
        return QColor(self.theme.ui[key] if self.theme else fallback)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), self._ui("panel", "#2b2d33"))
        border = self._ui("border", "#444")
        dim = self._ui("text_dim", "#999")
        text = self._ui("text", "#ddd")
        accent = self._ui("accent", "#6366f1")
        p.setPen(border)
        p.drawLine(0, 0, self.width(), 0)
        left = self.LEFT
        p.drawLine(left - 8, 0, left - 8, self.height())
        # título: número em destaque e nome embaixo, cortado com reticências
        number, _, name = self.title.partition("\n")
        f = QFont(p.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(text)
        p.drawText(QRect(12, 10, left - 28, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"Anim {number}" if number else "")
        f.setBold(False)
        p.setFont(f)
        p.setPen(dim)
        el = QFontMetrics(f).elidedText(name, Qt.TextElideMode.ElideRight, left - 28)
        p.drawText(QRect(12, 30, left - 28, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, el)
        if self.durations:
            n_frames = len(self.durations)
            p.drawText(QRect(12, 48, left - 28, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{n_frames} frames · {sum(self.durations)} t")
        total = max(1, sum(self.durations))
        w = self.width() - left - 12
        scale = w / total
        # régua: um rótulo a cada 'step' ticks, sem sobrepor números
        small = QFont(p.font().family(), 7)
        p.setFont(small)
        fm = QFontMetrics(small)
        step = 1
        for s in (1, 2, 5, 10, 20, 50, 100, 200, 500):
            step = s
            if s * scale >= fm.horizontalAdvance(str(total)) + 10:
                break
        p.setPen(dim)
        for t in range(0, total + 1, step):
            x = int(left + t * scale)
            p.drawLine(x, 4, x, 9)
            p.drawText(x + 3, 13, str(t))
        # frames
        acc = 0
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, h = 22, self.height() - 30
        for i, d in enumerate(self.durations):
            x0, x1 = left + acc * scale, left + (acc + d) * scale
            c = QColor(accent)
            cur = i == self.current_frame
            c.setAlpha(235 if cur else (150 if i % 2 else 90))
            p.setPen(QPen(text, 1.5) if cur else Qt.PenStyle.NoPen)
            p.setBrush(c)
            bw = max(2, int(x1 - x0) - 2)
            p.drawRoundedRect(int(x0) + 1, top, bw, h, 4, 4)
            label = str(i + 1)
            if bw > fm.horizontalAdvance(label) + 6:
                p.setPen(self._ui("accent_text", "#fff") if cur else text)
                p.drawText(QRect(int(x0) + 1, top, bw, h), Qt.AlignmentFlag.AlignCenter, label)
            if i == self.loopstart and i:
                p.setPen(QPen(self._ui("warning", "#fbbf24"), 2))
                p.drawLine(int(x0), top - 4, int(x0), top + h + 3)
            acc += d
        x = left + self.current_tick * scale
        p.setPen(QPen(text, 2))
        p.drawLine(int(x), 2, int(x), self.height() - 2)
        p.end()

    def mousePressEvent(self, e):  # noqa: N802
        total = max(1, sum(self.durations))
        t = int((e.position().x() - self.LEFT) / ((self.width() - self.LEFT - 12) / total))
        if 0 <= t <= total:
            self.tickClicked.emit(t)

    def mouseMoveEvent(self, e):  # noqa: N802
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.mousePressEvent(e)
