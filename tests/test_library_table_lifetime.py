"""Release Python-owned item wrappers outside Qt's recursive row destruction."""
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QPushButton
from digifly_app.ui.data_library import DataLibraryPage


def test_clear_takes_items_and_retires_widgets_before_native_row_destruction(monkeypatch):
    app=QApplication.instance() or QApplication([])
    table=QTableWidget(2,2)
    items=[QTableWidgetItem(str(i)) for i in range(4)]
    for i,item in enumerate(items):table.setItem(i//2,i%2,item)
    button=QPushButton('Old row action')
    table.setCellWidget(0,1,button)
    selected=[]
    table.itemSelectionChanged.connect(lambda:selected.append(True))
    table.selectRow(1)
    selected.clear()
    native=table.setRowCount
    def observed(count):
        assert count==0
        assert all(table.item(r,c) is None for r in range(2) for c in range(2))
        assert table.cellWidget(0,1) is None
        assert not button.isEnabled()
        native(count)
    monkeypatch.setattr(table,'setRowCount',observed)
    try:
        DataLibraryPage._clear_table_rows(table)
        assert table.rowCount()==0 and selected==[] and not table.signalsBlocked()
        assert [i.text() for i in items]==['0','1','2','3']
    finally:
        table.close();table.deleteLater();app.processEvents()


def test_empty_clear_restores_a_preexisting_signal_block():
    app=QApplication.instance() or QApplication([])
    table=QTableWidget(0,2)
    table.blockSignals(True)
    try:
        DataLibraryPage._clear_table_rows(table)
        assert table.signalsBlocked() and table.rowCount()==0
    finally:
        table.deleteLater();app.processEvents()
