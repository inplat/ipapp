from typing import Any, Dict, List

from docutils import nodes
from docutils.parsers.rst.directives.tables import Table
from sphinx.application import Sphinx

from .. import __version__


class Config(Table):

    headers = ["Название", "Тип", "По умолчанию", "Описание", "Пример"]

    def collect_rows(self) -> List[nodes.row]:
        def add_col(
            value: str, bold: bool = False, emphasis: bool = False
        ) -> nodes.entry:
            entry = nodes.entry()
            if bold:
                entry += nodes.strong(text=value)
            elif emphasis:
                entry += nodes.emphasis(text=value)
            else:
                entry += nodes.paragraph(text=value)
            return entry

        rows = []

        for name, params in self.env_schema.items():
            type_ = params.get("type", "")
            required = params.get("required", False)
            deprecated = params.get("deprecated", False)
            enum = params.get("enum", "")
            fmt = params.get("format", "")
            regex = params.get("regex", "")
            ge = params.get("ge", "")
            gt = params.get("gt", "")
            le = params.get("le", "")
            lt = params.get("lt", "")

            extra = []

            if enum:
                extra.append(", ".join(enum))

            if fmt:
                extra.append(fmt)

            if regex:
                extra.append(regex)

            if ge:
                extra.append(f">= {ge}")

            if gt:
                extra.append(f"> {gt}")

            if le:
                extra.append(f"<= {le}")

            if lt:
                extra.append(f"< {lt}")

            if extra:
                type_ = f"{type_} [{', '.join(extra)}]"

            bold = False
            if required:
                bold = True

            emphasis = False
            if deprecated:
                emphasis = True

            trow = nodes.row()
            trow += add_col(name, bold=bold, emphasis=emphasis)
            trow += add_col(type_, bold=bold, emphasis=emphasis)
            trow += add_col(
                params.get("default", ""), bold=bold, emphasis=emphasis
            )
            trow += add_col(
                params.get("description", ""), bold=bold, emphasis=emphasis
            )
            trow += add_col(
                params.get("example", ""), bold=bold, emphasis=emphasis
            )
            rows.append(trow)

        return rows

    def build_table(self) -> nodes.table:
        table = nodes.table()
        tgroup = nodes.tgroup(cols=len(self.headers))
        table += tgroup

        tgroup.extend(
            nodes.colspec(colwidth=col_width, colname="c" + str(idx))
            for idx, col_width in enumerate(self.col_widths)
        )

        thead = nodes.thead()
        tgroup += thead

        row_node = nodes.row()
        thead += row_node
        row_node.extend(
            nodes.entry(header, nodes.paragraph(text=header))
            for header in self.headers
        )

        tbody = nodes.tbody()
        tgroup += tbody

        rows = self.collect_rows()
        tbody.extend(rows)

        return table

    def run(self) -> List[nodes.table]:
        env = self.state.document.settings.env
        self.app = env.app

        config: Dict[str, Any] = self.app.config.ipapp_config
        if config is None:
            return []

        cls = config.get("class")
        if cls is None:
            return []

        prefix = config.get("prefix", "app_")

        self.max_cols = len(self.headers)
        self.col_widths = self.get_column_widths(self.max_cols)

        self.env_schema = cls.to_env_schema(prefix=prefix)

        table_node = self.build_table()
        self.add_name(table_node)

        return [table_node]


def setup(app: Sphinx) -> Dict[str, Any]:
    app.add_config_value('ipapp_config', {}, 'env')
    app.add_directive("config", Config)

    return {
        "version": __version__,
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
