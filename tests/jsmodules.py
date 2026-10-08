"""Just enough of a JavaScript reader to check how the frontend's modules fit together.

The frontend has no build step, so nothing compiles it before a browser does - and the
classic way to break a split into ES modules is a function that moved files without its
callers importing it, which fails only when somebody clicks the button that calls it.
This reads the modules well enough to catch that, and the other ways a module boundary
goes wrong: an import with no matching export, an import of a binding the importer then
assigns (imports are read-only), and the accidental globals strict mode forbids.

It is a tokeniser, not a parser: comments, strings, template literals (with their `${}`
expressions read as code) and regex literals are understood, and an identifier is
"used" unless it follows a dot or is an object key. What remains over-counts slightly,
which is the safe direction for every check below.
"""

import re

IDENT = re.compile(r"[A-Za-z_$][\w$]*")
# after one of these, a "/" starts a regex rather than dividing
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {"return", "typeof", "case", "do", "else",
                                              "in", "of", "new", "delete", "void", "throw"}
KEYWORDS = {
    "await", "break", "case", "catch", "class", "const", "continue", "debugger", "default",
    "delete", "do", "else", "export", "extends", "false", "finally", "for", "from", "function",
    "if", "import", "in", "instanceof", "let", "new", "null", "of", "return", "static",
    "super", "switch", "this", "throw", "true", "try", "typeof", "undefined", "var", "void",
    "while", "with", "yield", "async", "as", "get", "set",
}


def tokens(src):
    """(kind, text, depth) for identifiers and punctuation, skipping comments and string
    contents; template `${}` expressions are tokenised as code at their depth."""
    out, i, n, depth = [], 0, len(src), 0
    stack = []                      # template nesting: brace depth where each ${ opened

    def last():
        return out[-1][1] if out else ""

    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
        elif src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            out.append(("str", '""', depth))
        elif c == "`" or (c == "}" and stack and stack[-1] == depth):
            if c == "}":
                stack.pop()
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                elif src[j] == "`":
                    j += 1
                    break
                elif src.startswith("${", j):
                    stack.append(depth)
                    j += 2
                    break
                else:
                    j += 1
            i = j
            out.append(("str", "``", depth))
        elif c == "/" and (not out or last() in REGEX_AFTER):
            j, in_class = i + 1, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and (src[j].isalpha()):
                j += 1
            i = j
            out.append(("re", "/re/", depth))
        elif c.isdigit():
            m = re.match(r"\d[\w.]*", src[i:])
            i += m.end()
            out.append(("num", m.group(0), depth))
        else:
            m = IDENT.match(src, i)
            if m:
                out.append(("id", m.group(0), depth))
                i = m.end()
            else:
                if c in "{([":
                    out.append(("p", c, depth))
                    depth += 1
                elif c in "})]":
                    depth -= 1
                    out.append(("p", c, depth))
                else:
                    out.append(("p", c, depth))
                i += 1
    return out


def top_level_names(src):
    """Names declared at the top level: function, class, const, let, var."""
    names, toks = [], tokens(src)
    for k, (kind, text, depth) in enumerate(toks):
        if depth != 0 or kind != "id":
            continue
        if text in ("function", "class"):
            j = k + 2 if k + 1 < len(toks) and toks[k + 1][1] == "*" else k + 1   # function*
            if j < len(toks) and toks[j][0] == "id":
                names.append(toks[j][1])
        elif text in ("const", "let", "var") and k + 1 < len(toks) and toks[k + 1][0] == "id":
            names.append(toks[k + 1][1])
    return names


def used_names(src):
    """Identifiers used anywhere, other than as a property after a dot."""
    used, toks = set(), tokens(src)
    for k, (kind, text, _) in enumerate(toks):
        if kind == "id" and text not in KEYWORDS:
            if k and toks[k - 1][1] == ".":
                continue
            # an object key - `{ reroll: "Reroll" }` - is not a use of `reroll`. After `?`
            # it is the middle of a ternary, which is.
            if (k + 1 < len(toks) and toks[k + 1][1] == ":" and k
                    and toks[k - 1][1] in ("{", ",")):
                continue
            used.add(text)
    return used


def declared_names(src):
    """Every name declared anywhere in the module - top level, inner, parameters, catch
    bindings, loop variables - approximately. Used to spot assignments to names that are
    declared nowhere, which strict mode turns from an accidental global into an error."""
    names, toks = set(top_level_names(src)), tokens(src)
    for k, (kind, text, _) in enumerate(toks):
        if kind == "id" and text in ("const", "let", "var", "function", "class", "catch"):
            j = k + 1
            if text == "catch" and j < len(toks) and toks[j][1] == "(":
                j += 1
            while j < len(toks) and toks[j][1] in ("{", "[", ",", "...") or (
                    j < len(toks) and toks[j][0] == "id"
                    and j + 1 < len(toks) and toks[j + 1][1] in (",", "}", "]", ":", "=")):
                if toks[j][0] == "id":
                    names.add(toks[j][1])
                j += 1
                if j < len(toks) and toks[j - 1][1] in ("}", "]") and toks[j][1] in ("=", ";"):
                    break
            if j < len(toks) and toks[j][0] == "id":
                names.add(toks[j][1])
            # `let a = 0, b = f(1, 2), c;` declares a, b and c: every comma at the
            # declaration's own depth starts another, until its semicolon
            if text in ("const", "let", "var"):
                depth0 = toks[k][2]
                for j in range(k + 1, len(toks)):
                    kind_j, text_j, depth_j = toks[j]
                    if depth_j < depth0 or (text_j == ";" and depth_j == depth0):
                        break
                    if (text_j == "," and depth_j == depth0 and j + 1 < len(toks)
                            and toks[j + 1][0] == "id"):
                        names.add(toks[j + 1][1])
        # parameters: identifiers inside ( ... ) followed by => or {
        if text == "(":
            depth0, j, inner = toks[k][2], k + 1, []
            while j < len(toks) and not (toks[j][1] == ")" and toks[j][2] == depth0):
                if toks[j][0] == "id":
                    inner.append(toks[j][1])
                j += 1
            nxt = toks[j + 1][1] if j + 1 < len(toks) else ""
            if nxt in ("=>", "{") or (nxt == "=" and j + 2 < len(toks) and toks[j + 2][1] == ">"):
                names.update(inner)
        # a lone identifier parameter: x => ...
        if kind == "id" and k + 2 < len(toks) and toks[k + 1][1] == "=" and toks[k + 2][1] == ">":
            names.add(text)
    return names


def assigned_names(src):
    """Bare identifiers assigned to (`x =`, `x +=`, `x++`), not properties."""
    out, toks = set(), tokens(src)
    for k, (kind, text, _) in enumerate(toks):
        if kind != "id" or text in KEYWORDS or (k and toks[k - 1][1] == "."):
            continue
        nxt = toks[k + 1][1] if k + 1 < len(toks) else ""
        nxt2 = toks[k + 2][1] if k + 2 < len(toks) else ""
        if nxt == "=" and nxt2 not in ("=", ">"):
            # `const x =` and `let x =` declare rather than assign
            if k and toks[k - 1][1] in ("const", "let", "var"):
                continue
            out.add(text)
        elif nxt in ("+", "-", "*", "/", "|", "&") and nxt2 == "=":
            out.add(text)
        elif nxt in ("+", "-") and nxt2 == nxt:
            out.add(text)
    return out


IMPORT_RE = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"([^"]+)";?\s*$', re.M)
SIDE_IMPORT_RE = re.compile(r'^import\s*"([^"]+)";?\s*$', re.M)
EXPORT_DECL_RE = re.compile(r"^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)", re.M)


def imports(src):
    """{module path: [names]} from `import { a, b } from "./x.js"`."""
    found = {}
    for names, path in IMPORT_RE.findall(src):
        found.setdefault(path, []).extend(
            n.strip().split(" as ")[-1].strip() for n in names.split(",") if n.strip())
    return found


def imported_as(src):
    """{local name: (path, exported name)}."""
    out = {}
    for names, path in IMPORT_RE.findall(src):
        for n in names.split(","):
            n = n.strip()
            if n:
                orig, _, local = n.partition(" as ")
                out[(local or orig).strip()] = (path, orig.strip())
    return out


def side_imports(src):
    return SIDE_IMPORT_RE.findall(src)


def exports(src):
    return EXPORT_DECL_RE.findall(src)
