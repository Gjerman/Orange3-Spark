import sys
from functools import partial, reduce

import Orange
from Orange.widgets import gui, widget
from Orange.widgets.utils import itemmodels
from PyQt4 import QtCore
from PyQt4 import QtGui
from PyQt4.QtCore import Qt


def slices(indices):
    """ Group the given integer indices into slices
    """
    indices = list(sorted(indices))
    if indices:
        first = last = indices[0]
        for i in indices[1:]:
            if i == last + 1:
                last = i
            else:
                yield first, last + 1
                first = last = i
        yield first, last + 1


def source_model(view):
    """ Return the source model for the Qt Item View if it uses
    the QSortFilterProxyModel.
    """
    if isinstance(view.model(), QtGui.QSortFilterProxyModel):
        return view.model().sourceModel()
    else:
        return view.model()


def source_indexes(indexes, view):
    """ Map model indexes through a views QSortFilterProxyModel
    """
    model = view.model()
    if isinstance(model, QtGui.QSortFilterProxyModel):
        return list(map(model.mapToSource, indexes))
    else:
        return indexes


def delslice(model, start, end):
    """ Delete the start, end slice (rows) from the model.
    """
    if isinstance(model, itemmodels.PyListModel):
        del model[start:end]
    elif isinstance(model, QtCore.QAbstractItemModel):
        model.removeRows(start, end - start)
    else:
        raise TypeError(type(model))


class VariablesListItemModel(itemmodels.PyListModel):
    """ An Qt item model for for list of orange.Variable objects.
    Supports drag operations
    """

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid():
            flags |= Qt.ItemIsDragEnabled
        else:
            flags |= Qt.ItemIsDropEnabled
        return flags

    ###########
    # Drag/Drop
    ###########

    MIME_TYPE = "text/plain"

    def supportedDropActions(self):
        return Qt.MoveAction

    def supportedDragActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return [self.MIME_TYPE]

    def mimeData(self, indexlist):
        descriptors = []
        vars = []
        for index in indexlist:
            var = self[index.row()]
            descriptors.append(var)
            vars.append(var)
        mime = QtCore.QMimeData()
        mime.setData(self.MIME_TYPE, QtCore.QByteArray(str(descriptors)))
        mime._vars = vars
        return mime

    def dropMimeData(self, mime, action, row, column, parent):
        if action == Qt.IgnoreAction:
            return True
        vars = self.items_from_mime_data(mime)
        if vars is None:
            return False
        if row == -1:
            row = len(self)
        self[row:row] = vars
        return True

    def items_from_mime_data(self, mime):
        if not mime.hasFormat(self.MIME_TYPE):
            return None, None
        if hasattr(mime, "_vars"):
            vars = mime._vars
            return vars
        else:
            # TODO: get vars from orange.Variable.getExisting
            return None, None


class ClassVarListItemModel(VariablesListItemModel):
    def dropMimeData(self, mime, action, row, column, parent):
        """ Ensure only one variable can be dropped onto the view.
        """
        vars = self.items_from_mime_data(mime)
        if vars is None or len(self) + len(vars) > 1:
            return False
        if action == Qt.IgnoreAction:
            return True
        return VariablesListItemModel.dropMimeData(
            self, mime, action, row, column, parent)


class VariablesListItemView(QtGui.QListView):
    """ A Simple QListView subclass initialized for displaying
    variables.
    """

    def __init__(self, parent = None, acceptedType = str):
        super().__init__(parent)
        self.setSelectionMode(self.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(self.DragDrop)
        if hasattr(self, "setDefaultDropAction"):
            self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.viewport().setAcceptDrops(True)

        #: type | Tuple[type]
        self.__acceptedType = acceptedType

    def startDrag(self, supported_actions):
        indices = self.selectionModel().selectedIndexes()
        indices = [i for i in indices if i.flags() & Qt.ItemIsDragEnabled]
        if indices:
            data = self.model().mimeData(indices)
            if not data:
                return

            drag = QtGui.QDrag(self)
            drag.setMimeData(data)

            default_action = QtCore.Qt.IgnoreAction
            if hasattr(self, "defaultDropAction") and \
                            self.defaultDropAction() != Qt.IgnoreAction and \
                            supported_actions & self.defaultDropAction():
                default_action = self.defaultDropAction()
            elif (supported_actions & Qt.CopyAction and
                          self.dragDropMode() != self.InternalMove):
                default_action = Qt.CopyAction
            res = drag.exec_(supported_actions, default_action)
            if res == Qt.MoveAction:
                selected = self.selectionModel().selectedIndexes()
                rows = list(map(QtCore.QModelIndex.row, selected))
                for s1, s2 in reversed(list(slices(rows))):
                    delslice(self.model(), s1, s2)

    def dragEnterEvent(self, event):
        """
        Reimplemented from QListView.dragEnterEvent
        """
        if self.acceptsDropEvent(event):
            event.accept()
        else:
            event.ignore()

    def acceptsDropEvent(self, event):
        """
        Should the drop event be accepted?
        """
        # disallow drag/drops between windows
        if event.source() is not None and \
                        event.source().window() is not self.window():
            return False

        mime = event.mimeData()
        vars = source_model(self).items_from_mime_data(mime)
        if vars is None:
            return False

        if not all(isinstance(var, self.__acceptedType) for var in vars):
            return False

        event.accept()
        return True


class ClassVariableItemView(VariablesListItemView):
    def __init__(self, parent = None, acceptedType = str):
        VariablesListItemView.__init__(self, parent, acceptedType)
        self.setDropIndicatorShown(False)

    def acceptsDropEvent(self, event):
        """
        Reimplemented

        Ensure only one variable is in the model.
        """
        accepts = super().acceptsDropEvent(event)
        mime = event.mimeData()
        vars = source_model(self).items_from_mime_data(mime)
        if vars is None:
            return False

        if len(self.model()) + len(vars) > 1:
            return False

        return accepts


class VariableFilterProxyModel(QtGui.QSortFilterProxyModel):
    """ A proxy model for filtering a list of variables based on
    their names and labels.

    """

    def __init__(self, parent = None):
        super().__init__(parent)
        self._filter_string = ""

    def set_filter_string(self, filter):
        self._filter_string = str(filter).lower()
        self.invalidateFilter()

    def filter_accepts_variable(self, var):
        row_str = var
        row_str = row_str.lower()
        filters = self._filter_string.split()

        return all(f in row_str for f in filters)

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if isinstance(model, itemmodels.VariableListModel):
            var = model[source_row]
            return self.filter_accepts_variable(var)
        else:
            return True


class CompleterNavigator(QtCore.QObject):
    """ An event filter to be installed on a QLineEdit, to enable
    Key up/ down to navigate between posible completions.
    """

    def eventFilter(self, obj, event):
        if (event.type() == QtCore.QEvent.KeyPress and
                isinstance(obj, QtGui.QLineEdit)):
            if event.key() == Qt.Key_Down:
                diff = 1
            elif event.key() == Qt.Key_Up:
                diff = -1
            else:
                return False
            completer = obj.completer()
            if completer is not None and completer.completionCount() > 0:
                current = completer.currentRow()
                current += diff
                completer.setCurrentRow(current % completer.completionCount())
                completer.complete()
            return True
        else:
            return False


from orangecontrib.spark.base.shared_spark_context import SharedSparkContext
import pyspark
from pyspark.ml.feature import VectorAssembler


class OWSparkMLDatasetBuilder(SharedSparkContext, widget.OWWidget):
    priority = 5
    name = "Dataset Builder"
    description = "Select columns and assign them to data features and label columns."
    icon = "../icons/SelectColumns.svg"
    author = "Jose Antonio Martin H."
    author_email = "xjamartinh@gmail.com"
    inputs = [("DataFrame", pyspark.sql.DataFrame, "set_data", widget.Default)]
    outputs = [("DataFrame", pyspark.sql.DataFrame, widget.Dynamic)]

    want_main_area = False
    want_control_area = True

    domain_role_hints = None

    in_df = None
    out_df = None

    def __init__(self):
        super().__init__()
        self.controlArea = QtGui.QWidget(self.controlArea)
        self.layout().addWidget(self.controlArea)
        layout = QtGui.QGridLayout()
        self.controlArea.setLayout(layout)
        layout.setMargin(4)
        box = gui.widgetBox(self.controlArea, "Available Variables",
                            addToLayout = False)
        self.filter_edit = QtGui.QLineEdit()
        self.filter_edit.setToolTip("Filter the list of available variables.")
        box.layout().addWidget(self.filter_edit)
        if hasattr(self.filter_edit, "setPlaceholderText"):
            self.filter_edit.setPlaceholderText("Filter")

        self.completer = QtGui.QCompleter()
        self.completer.setCompletionMode(QtGui.QCompleter.InlineCompletion)
        self.completer_model = QtGui.QStringListModel()
        self.completer.setModel(self.completer_model)
        self.completer.setModelSorting(
            QtGui.QCompleter.CaseSensitivelySortedModel)

        self.filter_edit.setCompleter(self.completer)
        self.completer_navigator = CompleterNavigator(self)
        self.filter_edit.installEventFilter(self.completer_navigator)

        self.available_attrs = VariablesListItemModel()

        self.available_attrs_proxy = VariableFilterProxyModel()
        self.available_attrs_proxy.setSourceModel(self.available_attrs)
        self.available_attrs_view = VariablesListItemView(acceptedType = str)
        self.available_attrs_view.setModel(self.available_attrs_proxy)

        aa = self.available_attrs
        aa.dataChanged.connect(self.update_completer_model)
        aa.rowsInserted.connect(self.update_completer_model)
        aa.rowsRemoved.connect(self.update_completer_model)

        self.available_attrs_view.selectionModel().selectionChanged.connect(partial(self.update_interface_state, self.available_attrs_view))
        self.filter_edit.textChanged.connect(self.update_completer_prefix)
        self.filter_edit.textChanged.connect(self.available_attrs_proxy.set_filter_string)

        box.layout().addWidget(self.available_attrs_view)
        layout.addWidget(box, 0, 0, 3, 1)

        box = gui.widgetBox(self.controlArea, "features", addToLayout = False)
        self.used_attrs = VariablesListItemModel()
        self.used_attrs_view = VariablesListItemView(acceptedType = str)
        self.used_attrs_view.setModel(self.used_attrs)
        self.used_attrs_view.selectionModel().selectionChanged.connect(partial(self.update_interface_state, self.used_attrs_view))
        box.layout().addWidget(self.used_attrs_view)
        layout.addWidget(box, 0, 2, 1, 1)

        box = gui.widgetBox(self.controlArea, "label", addToLayout = False)
        self.class_attrs = VariablesListItemModel()
        self.class_attrs_view = VariablesListItemView(acceptedType = str)
        self.class_attrs_view.setModel(self.class_attrs)
        self.class_attrs_view.selectionModel().selectionChanged.connect(partial(self.update_interface_state, self.class_attrs_view))
        self.class_attrs_view.setMaximumHeight(24)
        box.layout().addWidget(self.class_attrs_view)
        layout.addWidget(box, 1, 2, 1, 1)

        box = gui.widgetBox(self.controlArea, "meta", addToLayout = False)
        self.meta_attrs = VariablesListItemModel()
        self.meta_attrs_view = VariablesListItemView(acceptedType = str)
        self.meta_attrs_view.setModel(self.meta_attrs)
        self.meta_attrs_view.selectionModel().selectionChanged.connect(partial(self.update_interface_state, self.meta_attrs_view))
        box.layout().addWidget(self.meta_attrs_view)
        layout.addWidget(box, 2, 2, 1, 1)

        bbox = gui.widgetBox(self.controlArea, addToLayout = False, margin = 0)
        layout.addWidget(bbox, 0, 1, 1, 1)

        self.up_attr_button = gui.button(bbox, self, "Up", callback = partial(self.move_up, self.used_attrs_view))
        self.move_attr_button = gui.button(bbox, self, ">", callback = partial(self.move_selected, self.used_attrs_view))
        self.down_attr_button = gui.button(bbox, self, "Down", callback = partial(self.move_down, self.used_attrs_view))

        bbox = gui.widgetBox(self.controlArea, addToLayout = False, margin = 0)
        layout.addWidget(bbox, 1, 1, 1, 1)
        self.move_class_button = gui.button(bbox, self, ">", callback = partial(self.move_selected, self.class_attrs_view, exclusive = True))

        bbox = gui.widgetBox(self.controlArea, addToLayout = False, margin = 0)
        layout.addWidget(bbox, 2, 1, 1, 1)
        self.up_meta_button = gui.button(bbox, self, "Up", callback = partial(self.move_up, self.meta_attrs_view))
        self.move_meta_button = gui.button(bbox, self, ">", callback = partial(self.move_selected, self.meta_attrs_view))
        self.down_meta_button = gui.button(bbox, self, "Down", callback = partial(self.move_down, self.meta_attrs_view))

        bbox = gui.widgetBox(self.controlArea, orientation = "horizontal", addToLayout = False, margin = 0)
        gui.button(bbox, self, "Apply", callback = self.commit)
        gui.button(bbox, self, "Reset", callback = self.reset)

        layout.addWidget(bbox, 3, 0, 1, 3)
        layout.setRowStretch(0, 4)
        layout.setRowStretch(1, 0)
        layout.setRowStretch(2, 2)
        layout.setHorizontalSpacing(0)
        self.controlArea.setLayout(layout)

        self.data = None
        self.output_report = None
        self.original_completer_items = []

        self.resize(500, 600)

    def set_data(self, data = None):
        self.update_domain_role_hints()
        self.data = data
        if self.data is not None:
            self.in_df = self.data
            # self.used_attrs.
            # self.class_attrs = VariablesListItemModel()
            # self.meta_attrs = VariablesListItemModel()
            self.available_attrs.extend(sorted(self.in_df.columns))

        else:
            self.data = None
            self.in_df = None
            # self.used_attrs = VariablesListItemModel()
            # self.class_attrs = VariablesListItemModel()
            # self.meta_attrs = VariablesListItemModel()
            # self.available_attrs = VariablesListItemModel()

    def update_domain_role_hints(self):
        """ Update the domain hints to be stored in the widgets settings.
        """
        hints_from_model = lambda role, model: [(attr, (role, i)) for i, attr in enumerate(model)]
        hints = dict(hints_from_model("available", self.available_attrs))
        hints.update(hints_from_model("attribute", self.used_attrs))
        hints.update(hints_from_model("class", self.class_attrs))
        hints.update(hints_from_model("meta", self.meta_attrs))
        self.domain_role_hints = hints

    def selected_rows(self, view):
        """ Return the selected rows in the view.
        """
        rows = view.selectionModel().selectedRows()
        model = view.model()
        if isinstance(model, QtGui.QSortFilterProxyModel):
            rows = [model.mapToSource(r) for r in rows]
        return [r.row() for r in rows]

    def move_rows(self, view, rows, offset):
        model = view.model()
        newrows = [min(max(0, row + offset), len(model) - 1) for row in rows]

        for row, newrow in sorted(zip(rows, newrows), reverse = offset > 0):
            model[row], model[newrow] = model[newrow], model[row]

        selection = QtGui.QItemSelection()
        for nrow in newrows:
            index = model.index(nrow, 0)
            selection.select(index, index)
        view.selectionModel().select(
            selection, QtGui.QItemSelectionModel.ClearAndSelect)

    def move_up(self, view):
        selected = self.selected_rows(view)
        self.move_rows(view, selected, -1)

    def move_down(self, view):
        selected = self.selected_rows(view)
        self.move_rows(view, selected, 1)

    def move_selected(self, view, exclusive = False):
        if self.selected_rows(view):
            self.move_selected_from_to(view, self.available_attrs_view)
        elif self.selected_rows(self.available_attrs_view):
            self.move_selected_from_to(self.available_attrs_view, view, exclusive)

    def move_selected_from_to(self, src, dst, exclusive = False):
        self.move_from_to(src, dst, self.selected_rows(src), exclusive)

    def move_from_to(self, src, dst, rows, exclusive = False):
        src_model = source_model(src)
        attrs = [src_model[r] for r in rows]

        if exclusive and len(attrs) != 1:
            return

        for s1, s2 in reversed(list(slices(rows))):
            del src_model[s1:s2]

        dst_model = source_model(dst)
        if exclusive and len(dst_model) > 0:
            src_model.append(dst_model[0])
            del dst_model[0]

        dst_model.extend(attrs)

    def update_interface_state(self, focus = None, selected = None, deselected = None):
        for view in [self.available_attrs_view, self.used_attrs_view,
                     self.class_attrs_view, self.meta_attrs_view]:
            if view is not focus and not view.hasFocus() and self.selected_rows(view):
                view.selectionModel().clear()

        def selected_vars(view):
            model = source_model(view)
            return [model[i] for i in self.selected_rows(view)]

        available_selected = selected_vars(self.available_attrs_view)
        attrs_selected = selected_vars(self.used_attrs_view)
        class_selected = selected_vars(self.class_attrs_view)
        meta_selected = selected_vars(self.meta_attrs_view)

        available_types = set(map(type, available_selected))
        all_primitive = all(True
                            for var in available_types)

        move_attr_enabled = (available_selected and all_primitive) or \
                            attrs_selected

        self.move_attr_button.setEnabled(bool(move_attr_enabled))
        if move_attr_enabled:
            self.move_attr_button.setText(">" if available_selected else "<")

        move_class_enabled = (len(available_selected) == 1 and all_primitive) or \
                             class_selected

        self.move_class_button.setEnabled(bool(move_class_enabled))
        if move_class_enabled:
            self.move_class_button.setText(">" if available_selected else "<")
        move_meta_enabled = available_selected or meta_selected

        self.move_meta_button.setEnabled(bool(move_meta_enabled))
        if move_meta_enabled:
            self.move_meta_button.setText(">" if available_selected else "<")

    def update_completer_model(self, *_):
        """ This gets called when the model for available attributes changes
        through either drag/drop or the left/right button actions.

        """
        vars = [att for att in self.available_attrs]
        items = [var for var in vars]
        labels = [label for label in vars]
        items.extend(["%s" % item for item in labels])
        items.extend(reduce(list.__add__, list(map(list, labels)), []))

        new = sorted(set(items))
        if new != self.original_completer_items:
            self.original_completer_items = new
            self.completer_model.setStringList(self.original_completer_items)

    def update_completer_prefix(self, filter):
        """ Prefixes all items in the completer model with the current
        already done completion to enable the completion of multiple keywords.
        """
        prefix = str(self.completer.completionPrefix())
        if not prefix.endswith(" ") and " " in prefix:
            prefix, _ = prefix.rsplit(" ", 1)
            items = [prefix + " " + item
                     for item in self.original_completer_items]
        else:
            items = self.original_completer_items
        old = list(map(str, self.completer_model.stringList()))

        if set(old) != set(items):
            self.completer_model.setStringList(items)

    def commit(self):
        self.update_domain_role_hints()
        if self.in_df is not None:
            attributes = [att for att in self.used_attrs._list]
            class_var = [var for var in self.class_attrs._list]
            metas = [meta for meta in self.meta_attrs._list]
            VA = VectorAssembler(inputCols = attributes, outputCol = 'features')
            self.out_df = VA.transform(self.in_df)
            if len(class_var):
                self.out_df = self.out_df.withColumn('label', self.out_df[class_var[0]].cast('double'))

            self.send("DataFrame", self.out_df)
        else:
            self.send("DataFrame", None)

    def reset(self):
        if self.data is not None:
            self.available_attrs.extend(sorted(self.in_df.columns))
        else:
            self.available_attrs._list = []

        self.used_attrs._list = []
        self.class_attrs._list = []
        self.meta_attrs._list = []
        self.update_domain_role_hints()


def test_main(argv = None):
    if argv is None:
        argv = sys.argv
    argv = list(argv)
    app = QtGui.QApplication(list(argv))

    if len(argv) > 1:
        filename = argv[1]
    else:
        filename = "brown-selected"

    w = OWSelectAttributes()
    data = Orange.data.Table(filename)
    w.set_data(data)
    w.show()
    w.raise_()
    rval = app.exec_()
    w.set_data(None)
    w.saveSettings()
    return rval


if __name__ == "__main__":
    sys.exit(test_main())
