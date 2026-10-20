"""Offline, version-explicit entry point to the existing neuPrint downloader."""
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from .widgets import Card


@dataclass(frozen=True)
class ConnectomeChoice:
    key: str
    name: str
    description: str
    releases: tuple[str, ...]


# Public /api/dbmeta/datasets snapshot, 2026-10-20. This catalogue is not a
# claim that these data are installed. The downloader rechecks the live server.
CONNECTOMES = (
    ConnectomeChoice('male_cns', 'Male CNS', 'Male brain and ventral nerve cord',
                     ('male-cns:v1.0', 'male-cns:v0.9')),
    ConnectomeChoice('manc', 'MANC', 'Male adult ventral nerve cord',
                     ('manc:v1.2.3', 'manc:v1.2.1', 'manc:v1.0')),
    ConnectomeChoice('hemibrain', 'Hemibrain', 'Female central brain reconstruction',
                     ('hemibrain:v1.2.1',)),
    ConnectomeChoice('optic_lobe', 'Optic Lobe', 'Male right optic lobe',
                     ('optic-lobe:v1.1', 'optic-lobe:v1.0.1')),
    ConnectomeChoice('mushroom_body', 'Mushroom Body', 'Mushroom body alpha lobe',
                     ('mushroombody',)),
)


def picture_path(key: str) -> Path:
    if key not in {choice.key for choice in CONNECTOMES}:
        raise ValueError('Unknown connectome picture')
    return Path(__file__).resolve().parents[1] / 'assets' / f'connectome_{key}.png'


class ConnectomePicker(Card):
    dataset_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAccessibleName('Connectome choices')
        self.selected_dataset = ''
        self.selected_key = ''
        self._selected_releases = {}
        self._columns = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel('What connectome do you want to work in?')
        title.setObjectName('SectionTitle')
        title.setWordWrap(True)
        layout.addWidget(title)
        help_label = QLabel('Choose a connectome to open its data tools here. You can choose its release on the next page.')
        help_label.setObjectName('Muted')
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid)
        self.cards, self.buttons, self.pictures = {}, {}, {}
        for choice in CONNECTOMES:
            card = Card()
            card.setMinimumWidth(220)
            body = QVBoxLayout(card)
            body.setContentsMargins(12, 12, 12, 12)
            body.setSpacing(8)
            image = QLabel()
            image.setObjectName('ConnectomePicture')
            image.setAccessibleName(f'{choice.name} picture from neuPrint')
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setFixedHeight(132)
            pixmap = QPixmap(str(picture_path(choice.key)))
            if pixmap.isNull():
                image.setText(f'{choice.name} picture unavailable')
            else:
                image.setPixmap(pixmap.scaled(270, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
            body.addWidget(image)
            button = QPushButton(choice.name)
            button.setObjectName('ConnectomeChoiceButton')
            button.setAccessibleName(f'Choose {choice.name} connectome')
            button.clicked.connect(lambda checked=False, key=choice.key: self.choose(key))
            body.addWidget(button)
            description = QLabel(choice.description)
            description.setObjectName('Muted')
            description.setWordWrap(True)
            description.setMinimumHeight(34)
            body.addWidget(description)
            self.cards[choice.key], self.buttons[choice.key] = card, button
            self.pictures[choice.key] = image
        credit = QLabel('Pictures: Janelia FlyEM and collaborators, as shown on '
                        '<a href="https://neuprint.janelia.org/">neuPrint</a> · '
                        '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. '
                        'Catalogue checked October 20, 2026; available releases are confirmed when you connect.')
        credit.setObjectName('Muted')
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        layout.addWidget(credit)
        self._reflow()

    def choose(self, key: str):
        choice = next(choice for choice in CONNECTOMES if choice.key == key)
        self.selected_key = key
        self.selected_dataset = self._selected_releases.get(key, choice.releases[0])
        self.dataset_selected.emit(self.selected_dataset)

    def select_release(self, dataset: str):
        choice = next(choice for choice in CONNECTOMES if choice.key == self.selected_key)
        if dataset not in choice.releases:
            raise ValueError('Release does not belong to the selected connectome')
        self.selected_dataset = dataset
        self._selected_releases[choice.key] = dataset

    def _reflow(self):
        columns = 3 if self.width() >= 950 else 2
        if columns == self._columns:
            return
        self._columns = columns
        for index, choice in enumerate(CONNECTOMES):
            self.grid.addWidget(self.cards[choice.key], index // columns, index % columns)
        for column in range(3):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()
