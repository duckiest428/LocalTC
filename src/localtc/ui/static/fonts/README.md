# Fonts

Both are served from this repository rather than from a font CDN, so loading the site makes no
third-party request and tells nobody that you visited. They are the latin-subset variable builds
Google Fonts serves, saved as-is.

| File | Family | Licence |
|---|---|---|
| `inter-latin-var.woff2` | [Inter](https://github.com/rsms/inter) by Rasmus Andersson | [SIL Open Font License 1.1](https://openfontlicense.org/) |
| `jetbrains-mono-latin-var.woff2` | [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) by JetBrains | [SIL Open Font License 1.1](https://openfontlicense.org/) |

The OFL permits bundling and redistribution like this, including within a project under another
licence. It requires the fonts to keep their names and not be sold on their own, neither of which
is a risk here.

To refresh them, take the `latin` `@font-face` block from
`https://fonts.googleapis.com/css2?family=Inter:wght@300..800&family=JetBrains+Mono:wght@400;500`
(requested with a modern browser's user agent) and download the `.woff2` it points at.
