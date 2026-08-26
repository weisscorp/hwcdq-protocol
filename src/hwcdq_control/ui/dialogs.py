"""Safety-critical dialogs owned by the desktop UI."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class SetpointConfirmationDialog(QDialog):
    """Show the exact requested setpoint and effective application maximum."""

    def __init__(
        self,
        *,
        title: str,
        value: float,
        maximum: float,
        unit: str,
        simulated: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("setpointConfirmationDialog")
        self.setWindowTitle(f"Подтвердить: {title.lower()}")
        self.setModal(True)
        self.setMinimumWidth(430)

        heading = QLabel(f"Записать новое значение: {title}")
        heading.setObjectName("setpointConfirmationHeading")
        heading.setStyleSheet("font-size: 17px; font-weight: 650;")
        source = "СИМУЛЯТОР" if simulated else "РЕАЛЬНОЕ УСТРОЙСТВО"
        summary = QLabel(
            f"{source}\n\n"
            f"Записываемое значение: {value:.2f} {unit}\n"
            f"Действующий максимум: {maximum:.2f} {unit}"
        )
        summary.setObjectName("setpointConfirmationSummary")
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        summary.setStyleSheet(
            "background: #F0F5F4; border: 1px solid #C4CCCE; "
            "padding: 12px; font-family: monospace;"
        )
        warning = QLabel(
            "После подтверждения приложение отправит одну изменяющую команду, "
            "затем должно перечитать конфигурацию. Автоматического повтора нет."
        )
        warning.setObjectName("setpointConfirmationWarning")
        warning.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.setObjectName("setpointConfirmationButtons")
        confirm = buttons.button(QDialogButtonBox.Ok)
        confirm.setText("Записать значение")
        confirm.setObjectName("confirmSetpointButton")
        confirm.setProperty("variant", "attention")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        cancel.setText("Отмена")
        cancel.setObjectName("cancelSetpointButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(13)
        layout.addWidget(heading)
        layout.addWidget(summary)
        layout.addWidget(warning)
        layout.addWidget(buttons)


class StartConfirmationDialog(QDialog):
    """Require an explicit attached-load acknowledgement before Start."""

    def __init__(
        self,
        volts: float,
        amps: float,
        *,
        simulated: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("startConfirmationDialog")
        self.setWindowTitle("Подтвердить включение выхода")
        self.setModal(True)
        self.setMinimumWidth(470)

        heading = QLabel("Включить выход зарядного устройства?")
        heading.setObjectName("startConfirmationHeading")
        heading.setStyleSheet("font-size: 17px; font-weight: 650;")

        source = "СИМУЛЯТОР — реальное устройство не затрагивается" if simulated else "РЕАЛЬНОЕ УСТРОЙСТВО"
        summary = QLabel(
            f"{source}\n\n"
            f"Целевое напряжение: {volts:.2f} V\n"
            f"Ограничение тока: {amps:.2f} A"
        )
        summary.setObjectName("startConfirmationSummary")
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        summary.setStyleSheet(
            "background: #F0F5F4; border: 1px solid #C4CCCE; "
            "padding: 12px; font-family: monospace;"
        )

        warning = QLabel(
            "Команда может немедленно подать напряжение. Проверьте полярность, "
            "допустимые параметры батареи или нагрузки и возможность безопасной остановки."
        )
        warning.setObjectName("startConfirmationWarning")
        warning.setWordWrap(True)

        self.acknowledge = QCheckBox(
            "Я проверил подключение нагрузки и подтверждаю эти значения"
        )
        self.acknowledge.setObjectName("safeLoadAcknowledgement")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.setObjectName("startConfirmationButtons")
        start_button = self.buttons.button(QDialogButtonBox.Ok)
        start_button.setText("Включить выход")
        start_button.setObjectName("confirmStartButton")
        start_button.setProperty("variant", "attention")
        start_button.setEnabled(False)
        cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        cancel_button.setText("Отмена")
        cancel_button.setObjectName("cancelStartButton")

        self.acknowledge.toggled.connect(start_button.setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(13)
        layout.addWidget(heading)
        layout.addWidget(summary)
        layout.addWidget(warning)
        layout.addWidget(self.acknowledge)
        layout.addWidget(self.buttons)


__all__ = ["SetpointConfirmationDialog", "StartConfirmationDialog"]
