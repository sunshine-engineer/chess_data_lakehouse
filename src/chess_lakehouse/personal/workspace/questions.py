"""Small explicit query grammar. Unsupported recent-game counts never widen scope."""
import re

WORDS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'few':3,'couple':2}
QUALIFIERS=r'(?:(?:white|black|blitz|rapid|bullet|classical|ultrabullet|lost|won|drawn)\s+)*'


def recent_count(question):
    """Parse a supported recent-game count and reject unsupported or unbounded requests."""
    text=question.casefold()
    pattern=r'\b(?:last|latest|recent|previous)\s+(?:a\s+)?(?P<count>\d+|'+ '|'.join(WORDS)+r')\s+'+QUALIFIERS+r'games?\b'
    match=re.search(pattern,text) or re.search(r'\b(?P<count>\d+)\s+(?:most\s+)?recent\s+'+QUALIFIERS+r'games?\b',text)
    if not match:match=re.search(r'\b(?:last|latest|recent|previous)\s+(?P<count>\d+|'+ '|'.join(WORDS)+r')\s+(?:wins?|losses|draws?)\b',text)
    if match:
        value=match['count'];count=int(value) if value.isdigit() else WORDS[value]
        if not 1<=count<=200:raise ValueError('Ask for between 1 and 200 recent games')
        return count
    if re.search(r'\b(?:recent|latest|last|previous)\s+(?:few\s+)?'+QUALIFIERS+r'games?\b',text):
        return 1 if re.search(r'\b(?:last|latest|previous)\s+game\b',text) else 3
    # Detect an unhandled count phrase instead of returning full-history results.
    if re.search(r'\b(?:last|latest|recent|previous)\b.{0,35}\b(?:games?|wins?|losses|draws?)\b',text) and not re.search(r'\b(?:days?|weeks?|months?|years?)\b',text):
        raise ValueError('Use a recent-game count, for example “Summarize my last 3 games”')
    return None
