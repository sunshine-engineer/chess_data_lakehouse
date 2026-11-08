"""Discover standard monthly exports and published SHA256s from Lichess HTTPS.

Torrent URLs are normalized to the matching official HTTPS archive; no torrent
client or peer network is needed. Redirects stay on the official HTTPS host.
"""
from html.parser import HTMLParser
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request,HTTPRedirectHandler,build_opener

BASE='https://database.lichess.org'
PATTERN=r'/standard/lichess_db_standard_rated_(\d{4}-(?:0[1-9]|1[0-2]))\.pgn\.zst'


def source_url(value):
    """Validate an official monthly link and normalize torrent links to HTTPS PGN downloads."""
    if not isinstance(value,str):raise ValueError('Provide an official standard archive link')
    parsed=urlsplit(value.strip())
    match=re.fullmatch(PATTERN+r'(?:\.torrent)?',parsed.path)
    if parsed.scheme!='https' or parsed.netloc!='database.lichess.org' or parsed.query or parsed.fragment or not match:
        raise ValueError('Use an HTTPS database.lichess.org/standard monthly .pgn.zst or .pgn.zst.torrent link')
    return match[1],BASE+parsed.path.removesuffix('.torrent')


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        url=urlsplit(newurl)
        if url.scheme!='https' or url.netloc!='database.lichess.org':raise ValueError('Archive redirect left the official HTTPS host')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def read(url,maximum=4*1024*1024):
    """Fetch size-bounded official metadata while restricting redirects to the trusted host."""
    with build_opener(OfficialRedirect()).open(Request(url,headers={'User-Agent':'chess-lakehouse/3'}),timeout=20) as response:
        content=response.read(maximum+1)
        if len(content)>maximum:raise ValueError('Official catalog exceeded metadata size limit')
        return content.decode('utf-8')


class Rows(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.cells=None;self.cell=None;self.urls=[]
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.cells=[];self.urls=[]
        elif tag=='td' and self.cells is not None:self.cell=[]
        elif tag=='a' and self.cells is not None:
            href=dict(attrs).get('href','')
            if href.startswith('standard/'):href=BASE+'/'+href
            if href.startswith('/standard/'):href=BASE+href
            if re.fullmatch(BASE+PATTERN,href):self.urls.append(href)
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag=='td' and self.cell is not None:self.cells.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.cells is not None:self.rows.append((self.cells,self.urls));self.cells=None;self.cell=None


def parse(html,checksums):
    """Extract standard monthly exports with matching published checksums from catalog HTML."""
    hashes={name:sha for sha,name in re.findall(r'^([a-f0-9]{64})\s+\*?(\S+)\s*$',checksums,re.M)}
    parser=Rows();parser.feed(html);result=[]
    for cells,urls in parser.rows:
        if len(cells)<3 or len(urls)!=1:continue
        month,url=source_url(urls[0]);sha=hashes.get(url.rsplit('/',1)[1])
        size=re.fullmatch(r'([\d.]+)\s*(kB|KB|MB|GB|TB)',cells[1])
        count=cells[2].replace(',','').replace(' ','')
        if not sha or not size or not count.isdigit():continue
        exponent={'kB':1,'KB':1,'MB':2,'GB':3,'TB':4}[size[2]]
        result.append(dict(month=month,url=url,sha256=sha,expected_games=int(count),display_size=cells[1],estimated_bytes=int(float(size[1])*1000**exponent)))
    if not result:raise ValueError('No checksum-backed standard monthly exports found; the official catalog format may have changed')
    return sorted(result,key=lambda r:r['month'],reverse=True)


_lock=threading.Lock();_cache=(0,[])
def catalog():
    """Return a short-lived cached copy of the official checksum-backed archive catalog."""
    global _cache
    with _lock:
        if time.monotonic()-_cache[0]>900 or not _cache[1]:
            _cache=(time.monotonic(),parse(read(BASE+'/'),read(BASE+'/standard/sha256sums.txt')))
        return [dict(row) for row in _cache[1]]


def select(value,rows=None):
    """Resolve a requested month or link and enforce the user's compressed-download ceiling."""
    wanted=source_url(value['url'])[0] if value.get('url') else value.get('month')
    rows=rows if rows is not None else catalog()
    row=next((r for r in rows if r['month']==wanted),None) if wanted else rows[0]
    if not row:raise ValueError('That month is not in the official checksum-backed catalog')
    maximum=value.get('max_download_bytes')
    if type(maximum) is not int or not 1<=maximum<=2*1024**4:raise ValueError('Set a compressed-download ceiling between 1 byte and 2 TiB')
    if row['estimated_bytes']>maximum:raise ValueError('The selected archive exceeds your compressed-download ceiling')
    return dict(row,max_download_bytes=maximum)
