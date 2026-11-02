"""Lightweight fakes to exercise GitLab helpers without Playwright."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import types


# Ensure the repo root is importable when running these tests directly.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Stub modules that api/__init__.py pulls in so imports succeed without external deps.
if "httpx" not in sys.modules:
    sys.modules["httpx"] = types.SimpleNamespace()
if "fastmcp" not in sys.modules:
    class _FakeFastMCP:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    sys.modules["fastmcp"] = types.SimpleNamespace(FastMCP=_FakeFastMCP)
if "pydantic" not in sys.modules:
    class _DummyModel:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    def _field(*_args: Any, **_kwargs: Any) -> None:
        return None

    sys.modules["pydantic"] = types.SimpleNamespace(BaseModel=_DummyModel, Field=_field)


class _DummyContext:
    """No-op context manager used for expect_load_state."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeLocator:
    """Minimal locator that supports the subset of Playwright APIs used in the SDK."""

    def __init__(
        self,
        text: str = "",
        html: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        children: Optional[List["FakeLocator"]] = None,
        nested: Optional[Dict[str, "FakeLocator"]] = None,
        count_value: Optional[int] = None,
        on_click: Optional[Callable[[], None]] = None,
        is_checked: bool = False,
        is_visible: bool = True,
        all_inner_texts_result: Optional[List[str]] = None,
    ) -> None:
        self.text = text
        self.html = html
        self.attributes = attributes or {}
        self.children = children or []
        self.nested = nested or {}
        self._is_checked = is_checked
        self._is_visible = is_visible
        self._all_inner_texts_result = all_inner_texts_result or []
        # Default count: if not specified, use len(children), but if we have content, default to 1
        if count_value is None:
            if len(self.children) > 0:
                self.count_value = len(self.children)
            elif text or html or self.attributes or self.nested:
                self.count_value = 1
            else:
                self.count_value = 0
        else:
            self.count_value = count_value
        self.actions: List[tuple[str, Any]] = []
        self.on_click = on_click

    def count(self) -> int:
        return self.count_value

    def nth(self, idx: int) -> "FakeLocator":
        if len(self.children) > 0:
            if 0 <= idx < len(self.children):
                return self.children[idx]
            return FakeLocator()
        if idx == 0 and self.count_value > 0:
            return self
        return FakeLocator()

    @property
    def first(self) -> "FakeLocator":
        if self.count_value > 0 and len(self.children) == 0:
            return self
        return self.nth(0)

    def locator(self, selector: str) -> "FakeLocator":
        result = self.nested.get(selector, FakeLocator())
        if result.count_value == 0 and (result.text or result.attributes or result.nested):
            result.count_value = 1
        return result

    def inner_text(self) -> str:
        return self.text

    def inner_html(self) -> str:
        return self.html if self.html is not None else self.text

    def text_content(self) -> Optional[str]:
        return self.text if self.text else None

    def all_inner_texts(self) -> List[str]:
        if self._all_inner_texts_result:
            return self._all_inner_texts_result
        return [child.text for child in self.children if child.text]

    def get_attribute(self, name: str) -> Optional[str]:
        value = self.attributes.get(name)
        return str(value) if value is not None else None

    def fill(self, value: Any) -> None:
        self.actions.append(("fill", value))
        self.text = str(value)

    def click(self) -> None:
        self.actions.append(("click", None))
        if self.on_click:
            self.on_click()

    def check(self) -> None:
        self.actions.append(("check", None))
        self._is_checked = True

    def uncheck(self) -> None:
        self.actions.append(("uncheck", None))
        self._is_checked = False

    def is_checked(self) -> bool:
        return self._is_checked

    def is_visible(self) -> bool:
        return self._is_visible

    def set_checked(self, value: bool) -> None:
        self.actions.append(("set_checked", value))
        self._is_checked = value

    def select_option(self, value: Optional[str] = None, **kwargs: Any) -> None:
        option = value if value is not None else kwargs.get("value") or kwargs.get("label")
        if kwargs.get("raise_error"):
            raise Exception("select_option failure")
        self.actions.append(("select", option))

    def set_input_files(self, files: Any) -> None:
        self.actions.append(("set_input_files", files))

    def evaluate(self, script: str) -> None:
        self.actions.append(("evaluate", script))

    def wait_for(self, **kwargs: Any) -> None:
        self.actions.append(("wait_for", kwargs))

    def scroll_into_view_if_needed(self) -> None:
        self.actions.append(("scroll_into_view_if_needed", None))


class FakePage:
    """Simple fake for Playwright's Page to drive the helpers."""

    def __init__(
        self,
        locators: Optional[Dict[str, FakeLocator]] = None,
        url: str = "",
        content_text: str = "",
    ) -> None:
        self.locators = locators or {}
        self.visited: List[str] = []
        self.url = url
        self._content_text = content_text
        self.soup = None
        self._viewport_size: Dict[str, int] = {"width": 1280, "height": 720}
        self._wait_for_selector_raises: Dict[str, bool] = {}
        self._input_values: Dict[str, str] = {}

    def expect_load_state(self, *_args: Any, **_kwargs: Any) -> _DummyContext:
        return _DummyContext()

    def goto(self, url: str, **kwargs: Any) -> None:
        self.url = url
        self.visited.append(url)

    def locator(self, selector: str) -> FakeLocator:
        if selector not in self.locators:
            self.locators[selector] = FakeLocator(count_value=0)
        return self.locators[selector]

    def get_by_role(self, role: str, **kwargs: Any) -> FakeLocator:
        key = f"role:{role}:{kwargs.get('name', '')}"
        if key not in self.locators:
            self.locators[key] = FakeLocator(count_value=0)
        return self.locators[key]

    def get_by_label(self, label: str) -> FakeLocator:
        key = f"label:{label}"
        if key not in self.locators:
            self.locators[key] = FakeLocator(count_value=0)
        return self.locators[key]

    def query_selector(self, selector: str) -> Optional[FakeLocator]:
        loc = self.locators.get(selector)
        if loc and loc.count_value > 0:
            return loc
        return None

    def fill(self, selector: str, value: Any) -> None:
        loc = self.locators.setdefault(selector, FakeLocator())
        loc.fill(value)

    def click(self, selector: str) -> None:
        loc = self.locators.setdefault(selector, FakeLocator(count_value=1))
        loc.click()

    def press(self, selector: str, key: str) -> None:
        loc = self.locators.setdefault(selector, FakeLocator())
        loc.actions.append(("press", key))

    def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        # Playwright accepts a timeout here; helpers pass one.
        pass

    def wait_for_timeout(self, timeout: int) -> None:
        pass

    def wait_for_selector(self, selector: str, **kwargs: Any) -> FakeLocator:
        if self._wait_for_selector_raises.get(selector):
            # Raise builtin TimeoutError which is mapped to playwright's
            raise TimeoutError("Timeout waiting for selector")
        return self.locator(selector)

    def set_viewport_size(self, size: Dict[str, int]) -> None:
        self._viewport_size = size

    def content(self) -> str:
        return self._content_text

    def input_value(self, selector: str) -> str:
        return self._input_values.get(selector, "")

    def title(self) -> str:
        return self.url

    def evaluate(self, script: str) -> Any:
        """Return an empty list — debug-only JS evaluation, not needed in unit tests."""
        return []

    def select_option(self, selector: str, value: str) -> None:
        loc = self.locators.setdefault(selector, FakeLocator())
        loc.select_option(value)


class FakeClient:
    """Client stub that mimics the HTML submission flow."""

    def __init__(self, pages: List[FakePage]) -> None:
        self.pages = pages
        self.current_page = pages[0] if pages else FakePage()
        self.submissions: List[Dict[str, Any]] = []

    def submit_form(self, form: Any, extra_data: Optional[Dict[str, Any]] = None) -> FakePage:
        self.submissions.append({"form": form, "extra_data": extra_data})
        if len(self.pages) > 1:
            self.pages.pop(0)
            self.current_page = self.pages[0]
        return self.current_page
