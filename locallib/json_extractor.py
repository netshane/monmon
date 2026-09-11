"""Extracts values from json documents using a jq-like path expression."""

from __future__ import annotations

import re


class JsonExtractor:
    """Resolves a subset of jq path syntax against parsed json.

    Supported forms:

    * `.a.b.c`            - nested object keys
    * `.a[0].b`           - explicit list index
    * `.a[].b`            - every element of a list (returns multiple values)
    * `.["odd key"].b`    - quoted keys
    * `.`                 - the document itself

    Keys are matched case sensitively first, then case insensitively, so a
    path of `.properties.count` also resolves `Properties.Count`.
    """

    TOKEN_PATTERN = re.compile(
        r"""
        \.\s*"(?P<quoted>[^"]+)"        # ."quoted key"
        | \.\s*\[\s*"(?P<bquoted>[^"]+)"\s*\]   # .["quoted key"]
        | \[\s*(?P<index>-?\d+)\s*\]    # [0]
        | \[\s*\]                       # []
        | \.\s*(?P<key>[A-Za-z_@$][\w\-]*)   # .key
        """,
        re.VERBOSE,
    )

    def extract_all(self, document, path: str) -> list:
        """Return every value the path resolves to (may be empty)."""
        tokens = self.parse_path(path)
        values = [document]

        for token in tokens:
            values = self._apply_token(values, token)

        return [v for v in values if v is not _MISSING]

    def extract_first(self, document, path: str, default=None):
        """Return the first value the path resolves to, or `default`."""
        values = self.extract_all(document, path)
        return values[0] if values else default

    def parse_path(self, path: str) -> list:
        path = (path or "").strip()
        if path in ("", "."):
            return []

        if not path.startswith(".") and not path.startswith("["):
            path = f".{path}"

        tokens: list = []
        position = 0
        while position < len(path):
            match = self.TOKEN_PATTERN.match(path, position)
            if not match:
                raise ValueError(
                    f"Unable to parse json path '{path}' at offset {position}"
                )

            if match.group("index") is not None:
                tokens.append(int(match.group("index")))
            elif match.group("key"):
                tokens.append(match.group("key"))
            elif match.group("quoted"):
                tokens.append(match.group("quoted"))
            elif match.group("bquoted"):
                tokens.append(match.group("bquoted"))
            else:
                tokens.append(_WILDCARD)

            position = match.end()

        return tokens

    def _apply_token(self, values: list, token) -> list:
        results: list = []

        for value in values:
            if value is _MISSING:
                continue

            if token is _WILDCARD:
                if isinstance(value, list):
                    results.extend(value)
                elif isinstance(value, dict):
                    results.extend(value.values())
                continue

            if isinstance(token, int):
                if isinstance(value, list):
                    try:
                        results.append(value[token])
                    except IndexError:
                        continue
                continue

            if isinstance(value, dict):
                found = self._get_key(value, token)
                if found is not _MISSING:
                    results.append(found)
                continue

            if isinstance(value, list):
                # jq-like convenience: map the key over a list of objects
                for item in value:
                    if isinstance(item, dict):
                        found = self._get_key(item, token)
                        if found is not _MISSING:
                            results.append(found)

        return results

    @staticmethod
    def _get_key(value: dict, key: str):
        if key in value:
            return value[key]

        lowered = key.lower()
        for existing_key, existing_value in value.items():
            if str(existing_key).lower() == lowered:
                return existing_value

        return _MISSING


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<missing>"


class _Wildcard:
    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<wildcard>"


_MISSING = _Missing()
_WILDCARD = _Wildcard()
