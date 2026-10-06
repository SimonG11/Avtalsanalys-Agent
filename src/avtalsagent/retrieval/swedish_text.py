"""The text analyser of the BM25 word index: Swedish text to stems.

What:
    `words` splits text into lowercased tokens, `terms` turns a chunk's text
    into the stems the index counts (stop words removed, in order), and
    `query_terms` gives the unique stems of a question. `ANALYSER` names this
    analyser and the stemmer's version; step 6 stores it with the index.

Why:
    BM25 compares stems, so "avtalet", "avtalen" and "avtal" must give the
    same one, and the question must be analysed exactly as the chunks were.
    PostgreSQL 17's `to_tsvector('swedish')` is not used: its stemmer leaves
    "avtalet" apart from "avtal" (Snowball 3.0 fixed this), it keeps "ska"
    and "skall" (in almost every clause, so they say nothing), and it cuts
    "leverantör/underleverantör" into "leverantör", "/underleverant" and
    "ör". Doing it in Python also lets the offline measurement use the very
    same function (ADR 0011).

How:
    1. The text is put in Unicode NFC form, so an "ä" typed as "a" plus a
       combining diaeresis is the same letter, and lowercased. å, ä and ö
       stay: "får" is not "far".
    2. A regular expression takes, at each place, the first of: a case or
       agreement number, a date, an organisation number, a section number,
       and a run of letters and digits. Identifiers are kept whole; any other
       character, such as a hyphen or a slash, separates words ("IT-drift"
       is "it" and "drift").
    3. Stop words are dropped: PostgreSQL 17's Swedish list plus "ska" and
       "skall".
    4. A word of letters only is stemmed with the Snowball Swedish stemmer;
       identifiers, numbers and words with digits are kept as they are.

    Changing any rule changes the index: raise the version in `ANALYSER`, and
    the search refuses an index built with the old one until `index` is run.
"""

import re
import unicodedata
from functools import lru_cache
from importlib.metadata import version

# The pure Python stemmer, imported directly: `snowballstemmer.stemmer()` returns
# PyStemmer's C stemmer instead when that is installed, whose algorithm version can
# differ, and the stems would then depend on the machine. The package has no types.
from snowballstemmer.swedish_stemmer import SwedishStemmer  # type: ignore[import-untyped]

ANALYSER = f"sv-1 snowballstemmer {version('snowballstemmer')}"

# PostgreSQL 17's Swedish stop list, src/backend/snowball/stopwords/swedish.stop
# (https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/snowball/stopwords/swedish.stop),
# 114 words in its order, the list `to_tsvector('swedish')` uses. A string, not a list
# literal, so it reads like the file.
_POSTGRESQL_STOP_WORDS = """
och det att i en jag hon som han på den med var sig för så till är men ett om hade de av
icke mig du henne då sin nu har inte hans honom skulle hennes där min man ej vid kunde
något från ut när efter upp vi dem vara vad över än dig kan sina här ha mot alla under
någon eller allt mycket sedan ju denna själv detta åt utan varit hur ingen mitt ni bli
blev oss din dessa några deras blir mina samma vilken er sådan vår blivit dess inom
mellan sådant varför varje vilka ditt vem vilket sitta sådana vart dina vars vårt våra
ert era vilkas
""".split()  # noqa: SIM905
# "ska" and "skall" state nearly every obligation in an agreement, so they tell no
# clause from another; PostgreSQL's list (Snowball's) has neither.
STOP_WORDS = frozenset(_POSTGRESQL_STOP_WORDS) | {"ska", "skall"}

# Tried in this order at each place in the (lowercased) text.
_TOKEN = re.compile(
    r"""
    # Kammarkollegiet's case number, with the supplier's sequence when there is one:
    # 23.3-2649-2022-003, 23.3.2649-22-003, 23.3-2940-20:033, 23.3-4613-2023-003-a
    23\.\d[-–.]\d{2,7}[-–]\d{2,4}(?:[-–:]\d{2,3}(?:-[a-z])?)?(?![^\W_])
    # its older letterhead form: 96-38-2011
    | 9\d-\d{1,6}-(?:19|20)\d{2}(?![^\W_])
    # a date: 2025-08-19
    | \d{4}-\d{2}-\d{2}(?![^\W_])
    # an organisation number: 556635-9799
    | \d{6}-\d{4}(?![^\W_])
    # a section number: 6.21.9
    | \d+(?:\.\d+)+
    # a word, a number, or both run together: avtalet, 200, iso27001
    | [^\W_]+
    """,
    re.VERBOSE,
)


def words(text: str) -> list[str]:
    """The lowercased tokens of `text` in order, before stop words and stemming."""
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


def terms(text: str) -> list[str]:
    """The stems of `text` in order, stop words removed: what BM25 counts in a chunk."""
    return [_stem(word) for word in words(text) if word not in STOP_WORDS]


def query_terms(text: str) -> list[str]:
    """The unique stems of a question, in the order they first appear."""
    return list(dict.fromkeys(terms(text)))


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    if not word.isalpha():
        return word
    # A new stemmer for each word: one keeps its state in the instance, so sharing
    # one between threads is unsafe, and the cache makes this rare (the pilot has
    # some 20,000 distinct words).
    stem: str = SwedishStemmer().stemWord(word)
    return stem
