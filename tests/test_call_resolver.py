"""Tests for call_resolver.py."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.call_resolver import _extract_call_name, _is_builtin, resolve_call
from paxpy.types import FunctionIndex, FunctionLocation

FILEPATH = Path("/repo/mod.py")


def make_index(*funcs: tuple[str, str]) -> FunctionIndex:
    """Build a FunctionIndex from (name, filepath) pairs."""
    index: dict[str, list[FunctionLocation]] = {}
    for name, fpath in funcs:
        loc = FunctionLocation(name=name, filepath=Path(fpath), lineno=1, end_lineno=5)
        index.setdefault(name, []).append(loc)
    return FunctionIndex(index=index)


def parse_call(expr: str) -> ast.Call:
    """Parse a call expression string and return the ast.Call node."""
    tree = ast.parse(expr, mode="eval")
    assert isinstance(tree.body, ast.Call)
    return tree.body


def _add_parents(tree: ast.AST) -> None:
    """Add _parent pointers to every node in the tree (mirrors ensure_parsed)."""
    tree._parent = None  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def parse_with_parents(source: str) -> ast.Module:
    """Parse source and attach parent pointers throughout the tree."""
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    _add_parents(tree)
    return tree


def find_calls(tree: ast.AST, attr_name: str | None = None) -> list[ast.Call]:
    """Collect all ast.Call nodes; if attr_name given, filter to self.<attr_name>()."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if attr_name is None or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == attr_name
        ):
            calls.append(node)
    return calls


def make_index_with_locs(*locs: FunctionLocation) -> FunctionIndex:
    """Build a FunctionIndex directly from FunctionLocation objects."""
    index: dict[str, list[FunctionLocation]] = {}
    for loc in locs:
        index.setdefault(loc.name, []).append(loc)
    return FunctionIndex(index=index)


# ---------------------------------------------------------------------------
# _extract_call_name
# ---------------------------------------------------------------------------


def test_extract_simple_name():
    call = parse_call("foo()")
    assert _extract_call_name(call) == "foo"


def test_extract_attribute_name():
    call = parse_call("obj.method()")
    assert _extract_call_name(call) == "method"


def test_extract_chained_attribute():
    call = parse_call("a.b.method()")
    assert _extract_call_name(call) == "method"


def test_extract_subscript_returns_none():
    call = parse_call("funcs[0]()")
    assert _extract_call_name(call) is None


def test_extract_lambda_call_returns_none():
    # (lambda: None)() — the func is a Lambda node
    call = parse_call("(lambda: None)()")
    assert _extract_call_name(call) is None


# ---------------------------------------------------------------------------
# _is_builtin
# ---------------------------------------------------------------------------


def test_is_builtin_len():
    assert _is_builtin("len") is True


def test_is_builtin_print():
    assert _is_builtin("print") is True


def test_is_builtin_range():
    assert _is_builtin("range") is True


def test_is_builtin_user_func():
    assert _is_builtin("my_custom_func") is False


def test_is_builtin_type():
    assert _is_builtin("type") is True


# ---------------------------------------------------------------------------
# resolve_call
# ---------------------------------------------------------------------------


def test_resolve_indexed_function():
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("foo()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 1
    assert result[0].name == "foo"


def test_resolve_ambiguous_returns_all():
    index = make_index(("process", "/repo/a.py"), ("process", "/repo/b.py"))
    call = parse_call("process()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 2
    assert all(r.name == "process" for r in result)


def test_resolve_builtin_returns_empty():
    index = make_index()  # empty index
    call = parse_call("len(x)")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_unknown_external_returns_empty():
    index = make_index()
    call = parse_call("requests_get()")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_attribute_call_by_method_name():
    """obj.process() resolves to all functions named 'process' (over-approx)."""
    index = make_index(("process", "/repo/a.py"))
    call = parse_call("self.process()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 1
    assert result[0].name == "process"


def test_resolve_attribute_not_in_index_returns_empty():
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("obj.unknown_method()")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_subscript_call_returns_empty():
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("funcs[0]()")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_indexed_function_shadows_builtin():
    """If user defines a function named 'len', it appears in index and is returned."""
    index = make_index(("len", "/repo/a.py"))
    call = parse_call("len(x)")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 1
    assert result[0].filepath == Path("/repo/a.py")


def test_resolve_returns_list_copy():
    """Result should be a copy, not the live index list."""
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("foo()")
    result = resolve_call(call, index, FILEPATH)
    result.clear()
    assert len(index.lookup("foo")) == 1


# ---------------------------------------------------------------------------
# Self-method resolution
# ---------------------------------------------------------------------------


def test_self_method_resolves_to_same_class():
    """self.process() in MyClass resolves only to MyClass.process, not to an
    unrelated process() in another file."""
    source = """
        class MyClass:
            def run(self):
                self.process()
            def process(self):
                pass
    """
    tree = parse_with_parents(source)

    # Locate MyClass.process (lineno 5 in dedented source — ast gives 1-based)
    class_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    process_method = next(
        n
        for n in ast.iter_child_nodes(class_def)
        if isinstance(n, ast.FunctionDef) and n.name == "process"
    )
    my_file = Path("/repo/mymod.py")
    other_file = Path("/repo/other.py")

    loc_mine = FunctionLocation(
        name="process",
        filepath=my_file,
        lineno=process_method.lineno,
        end_lineno=process_method.end_lineno,
        ast_node=process_method,
    )
    loc_other = FunctionLocation(
        name="process",
        filepath=other_file,
        lineno=1,
        end_lineno=5,
    )

    index = make_index_with_locs(loc_mine, loc_other)
    index.parsed_asts[my_file] = tree

    # Get the self.process() call node (has parent pointers)
    [call] = find_calls(tree, "process")
    result = resolve_call(call, index, my_file)

    assert len(result) == 1
    assert result[0].filepath == my_file
    assert result[0].lineno == process_method.lineno


def test_self_method_no_parent_pointers_falls_back():
    """self.foo() without parent pointers falls back to all-names over-approx."""
    index = make_index(("process", "/repo/a.py"), ("process", "/repo/b.py"))
    call = parse_call("self.process()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 2
    assert all(r.name == "process" for r in result)


def test_self_method_with_base_class_includes_inherited_method():
    """self.foo() in a subclass includes base class method when base is parseable."""
    source = """
        class Base:
            def validate(self):
                pass

        class Child(Base):
            def run(self):
                self.validate()
    """
    tree = parse_with_parents(source)

    base_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Base")
    validate_in_base = next(
        n
        for n in ast.iter_child_nodes(base_def)
        if isinstance(n, ast.FunctionDef) and n.name == "validate"
    )
    my_file = Path("/repo/mymod.py")

    loc_base = FunctionLocation(
        name="validate",
        filepath=my_file,
        lineno=validate_in_base.lineno,
        end_lineno=validate_in_base.end_lineno,
        ast_node=validate_in_base,
    )
    loc_other = FunctionLocation(
        name="validate",
        filepath=Path("/repo/other.py"),
        lineno=1,
        end_lineno=5,
    )

    index = make_index_with_locs(loc_base, loc_other)
    index.parsed_asts[my_file] = tree

    [call] = find_calls(tree, "validate")
    result = resolve_call(call, index, my_file)

    # Should include the base-class method but NOT the unrelated one in other.py
    filepaths = {r.filepath for r in result}
    assert my_file in filepaths
    assert Path("/repo/other.py") not in filepaths


def test_self_method_unknown_name_falls_back():
    """self.unknown() where no candidate exists returns empty list."""
    source = """
        class MyClass:
            def run(self):
                self.unknown()
    """
    tree = parse_with_parents(source)
    my_file = Path("/repo/mymod.py")
    index = FunctionIndex(index={}, parsed_asts={my_file: tree})

    [call] = find_calls(tree, "unknown")
    result = resolve_call(call, index, my_file)
    assert result == []


# ---------------------------------------------------------------------------
# Import-scoped resolution
# ---------------------------------------------------------------------------


def _make_file_index_with_source(
    source_by_path: dict[Path, str],
) -> FunctionIndex:
    """Build a FunctionIndex from a mapping of filepath → source code.

    Parses each file, adds parent pointers, and populates the index with
    FunctionLocation entries at the correct line numbers.
    """
    raw_index: dict[str, list[FunctionLocation]] = {}
    parsed: dict[Path, ast.Module] = {}

    for filepath, src in source_by_path.items():
        tree = parse_with_parents(textwrap.dedent(src))
        parsed[filepath] = tree
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                loc = FunctionLocation(
                    name=node.name,
                    filepath=filepath,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno,
                    ast_node=node,
                )
                raw_index.setdefault(node.name, []).append(loc)

    return FunctionIndex(index=raw_index, parsed_asts=parsed)


def test_import_scoped_resolves_to_imported_module(tmp_path: Path):
    """bare process() with 'from utils import process' resolves only to utils.py."""
    utils_file = tmp_path / "utils.py"
    caller_file = tmp_path / "caller.py"

    utils_src = """
        def process():
            pass
    """
    caller_src = """
        from utils import process

        def main():
            process()
    """

    utils_file.write_text(textwrap.dedent(utils_src))
    caller_file.write_text(textwrap.dedent(caller_src))

    index = _make_file_index_with_source({utils_file: utils_src, caller_file: caller_src})
    # Also add an unrelated process in another file to confirm narrowing
    other_file = tmp_path / "other.py"
    other_src = "def process(): pass\n"
    other_file.write_text(other_src)
    parse_with_parents(other_src)  # ensure parseable
    other_loc = FunctionLocation(
        name="process",
        filepath=other_file,
        lineno=1,
        end_lineno=1,
    )
    index.index.setdefault("process", []).append(other_loc)

    caller_tree = index.parsed_asts[caller_file]
    [call] = [
        n
        for n in ast.walk(caller_tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "process"
    ]
    result = resolve_call(call, index, caller_file)

    filepaths = {r.filepath for r in result}
    assert utils_file in filepaths
    assert other_file not in filepaths


def test_import_scoped_no_import_falls_back():
    """bare process() with no import returns all matches (over-approximation)."""
    index = make_index(("process", "/repo/a.py"), ("process", "/repo/b.py"))
    call = parse_call("process()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Same-file preference
# ---------------------------------------------------------------------------


def test_same_file_local_definition_comes_first():
    """When process() is defined locally AND in another file, local comes first."""
    local_file = Path("/repo/mymod.py")
    other_file = Path("/repo/other.py")

    loc_local = FunctionLocation(name="process", filepath=local_file, lineno=1, end_lineno=5)
    loc_other = FunctionLocation(name="process", filepath=other_file, lineno=1, end_lineno=5)

    index = make_index_with_locs(loc_local, loc_other)

    call = parse_call("process()")
    result = resolve_call(call, index, local_file)

    assert len(result) == 2
    assert result[0].filepath == local_file, "local definition should be first"
    assert result[1].filepath == other_file


def test_same_file_no_local_returns_all():
    """When process() has no local definition, all remote candidates returned."""
    local_file = Path("/repo/mymod.py")

    loc_a = FunctionLocation(name="process", filepath=Path("/repo/a.py"), lineno=1, end_lineno=5)
    loc_b = FunctionLocation(name="process", filepath=Path("/repo/b.py"), lineno=1, end_lineno=5)

    index = make_index_with_locs(loc_a, loc_b)
    call = parse_call("process()")
    result = resolve_call(call, index, local_file)

    assert len(result) == 2
    assert {r.filepath for r in result} == {Path("/repo/a.py"), Path("/repo/b.py")}


# ---------------------------------------------------------------------------
# Fallback: common names with no narrowing cues
# ---------------------------------------------------------------------------


def test_fallback_common_name_returns_all():
    """A common name with no import and no parent pointers returns all matches."""
    index = make_index(
        ("handle", "/repo/a.py"),
        ("handle", "/repo/b.py"),
        ("handle", "/repo/c.py"),
    )
    call = parse_call("handle()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 3
    assert all(r.name == "handle" for r in result)


def test_fallback_attribute_no_self_returns_all():
    """obj.handle() (non-self attribute) returns all functions named handle."""
    index = make_index(
        ("handle", "/repo/a.py"),
        ("handle", "/repo/b.py"),
    )
    call = parse_call("obj.handle()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 2
