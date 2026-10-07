"""Why an uploaded file is refused: an HTTP status and a Swedish text for the user.

What:
    `UploadRejected(status_code, detail)`, raised while a file is checked
    and read, the texts it carries, and `thousands(number)`, a number in
    the texts as Swedish writes it.

Why:
    The checks run in `uploads/`, apart from the routes, and the route
    answers with the status and text they chose (webbapp-kontrakt.md, point
    33): 413 for a file that is too large in some way, 415 for a file type
    that is not read, and 422 for a file that is the right type but has no
    text that can be read, or that takes too long to read. The texts are
    shown in the web app as they are.

How:
    One exception class with the status and the text; the route turns it
    into an `HTTPException`.
"""


class UploadRejected(Exception):
    """A file that is not stored: `status_code` (413, 415 or 422) and `detail` in Swedish."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


TOO_LARGE = "Filen är för stor. Den får vara högst {limit}."
TOO_MANY_PAGES = "PDF:en har {pages} sidor. Den får ha högst {limit}."
TOO_MUCH_TEXT = "Filen har för mycket text ({characters} tecken). Den får ha högst {limit}."
# For a file whose reading stopped as soon as it passed the limit, so the total is not known.
TOO_MUCH_TEXT_OVER = "Filen har mer än {limit} tecken text. Den får ha högst {limit}."
TOO_MANY_PARAGRAPHS = "Word-filen har {count} stycken och tabeller. Den får ha högst {limit}."
TOO_HEAVY = "Filen kräver för mycket minne för att läsas. Prova en mindre fil."
TOO_SLOW = "Filen tog för lång tid att läsa. Prova en mindre fil."
UNPACKED_TOO_LARGE = "Word-filen blir för stor när den packas upp och läses därför inte."
UNSUPPORTED = (
    "Filtypen stöds inte. Ladda upp en PDF med text, en Word-fil (.docx) eller text (.txt, .md)."
)
OLD_WORD = "Äldre Word-filer (.doc) stöds inte. Spara filen som .docx och ladda upp den igen."
MISMATCH = "Filens innehåll är {found}, men namnet slutar på {suffix}. Är det rätt fil?"
UNREADABLE = "Filen gick inte att läsa. Den kan vara skadad eller skyddad med lösenord."
SCANNED = (
    "PDF:en har ingen text att läsa, bara bilder. Inskannade filer stöds inte ännu; "
    "ladda upp en PDF med text eller filen i Word."
)
NO_TEXT = "Filen innehåller ingen text."
NOT_UTF8 = "Textfilen gick inte att läsa som UTF-8. Spara den som UTF-8 och ladda upp den igen."
EMPTY = "Filen är tom."


def thousands(number: int) -> str:
    """A number with (non-breaking) spaces between thousands, as Swedish writes it: "1 500 000"."""
    return f"{number:,}".replace(",", "\u00a0")
