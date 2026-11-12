"""Public Shopify -> Google free-listing files. No Shopify or Google credentials."""
from __future__ import annotations
import argparse, concurrent.futures, csv, datetime as dt, decimal, hashlib, html
from html.parser import HTMLParser
import json, os, pathlib, re, ssl, subprocess, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from email.utils import parsedate_to_datetime

ROOT = pathlib.Path(__file__).resolve().parent
SHOP = 'https://lidomarketplus.it'
UTC = dt.timezone.utc
SSL_CONTEXT=ssl.create_default_context(cafile='/etc/ssl/cert.pem' if pathlib.Path('/etc/ssl/cert.pem').exists() else None)
FETCH_LOCK=threading.Lock()
LAST_FETCH=0.0
GLOBAL_COOLDOWN=0.0
FIELDS = ['id','title','description','link','image_link','additional_image_link','availability','price','condition','gtin','brand','shipping_label','included_destination','excluded_destination','expiration_date']
BANNED = re.compile(r'sunlux|progress[ _-]?plastic|sun[ _-]?distribuzione', re.I)
CHINESE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]')
MOQ = re.compile(r'minimo\s+(?:ordine|d[’\']ordine)|ordine\s+minimo|quantit[aà]\s+minima|minimum\s+(?:order|quantity|purchase)|min\.?\s+order|mindestbestellmenge|mindestbestellung|mindestmenge|mindestabnahme|minimale\s+bestellmenge|commande\s+minim(?:um|ale)|quantit[eé]\s+minim(?:um|ale)|minimum\s+de\s+commande|pedido\s+m[ií]nimo|cantidad\s+m[ií]nima|m[ií]nimo\s+de\s+(?:pedido|compra)|minimum\s*bestell?ing|minimum\s*bestelaantal|minimale\s+(?:bestelhoeveelheid|bestelling|bestelgrootte|hoeveelheid)|minimum\s*afname|minimumbestelhoeveelheid', re.I)

class FeedError(RuntimeError): pass
class Text(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.skip+=1
        if tag in {'p','br','li','div','tr','h1','h2','h3','h4'}: self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.skip=max(0,self.skip-1)
        if tag in {'p','li','div','tr','h1','h2','h3','h4'}: self.parts.append(' ')
    def handle_data(self, text):
        if not self.skip: self.parts.append(text)

def plain(value):
    parser=Text(); parser.feed(value or '')
    return re.sub(r'\s+',' ',html.unescape(''.join(parser.parts))).strip()

def gtin(value):
    value=str(value or '').strip()
    if len(value) not in {8,12,13,14} or not value.isascii() or not value.isdigit(): return ''
    if value.startswith(('02','04','2','05','98','99')) or set(value)=={'0'}: return ''
    total=sum(int(c)*(3 if i%2==0 else 1) for i,c in enumerate(reversed(value[:-1])))
    return value if (10-total%10)%10==int(value[-1]) else ''

def get_json(url):
    """Use the real curl client, without spoofed user agents, cookies, authentication or proxy rotation."""
    global LAST_FETCH, GLOBAL_COOLDOWN
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='lidomarketplus.it': raise FeedError('Non-store fetch rejected')
    for attempt in range(5):
        while True:
            with FETCH_LOCK:
                delay=max(0,GLOBAL_COOLDOWN-time.monotonic(),0.5-(time.monotonic()-LAST_FETCH))
                if delay<=0:
                    LAST_FETCH=time.monotonic()
                    break
            time.sleep(min(delay,5))
        marker='\n__LIDO_HTTP__\n'
        command=['curl','--disable','--silent','--show-error','--location','--max-redirs','3','--proto','=https','--proto-redir','=https','--max-time','65','--write-out',marker+'%{http_code}\n%header{retry-after}\n%{url_effective}\n',url]
        result=subprocess.run(command,capture_output=True,text=True,timeout=75)
        body,separator,metadata=result.stdout.rpartition(marker)
        lines=metadata.splitlines()
        if result.returncode or not separator or len(lines)<3:
            if attempt==4: raise FeedError('curl transport failure; no partial JSON accepted')
            wait=2**(attempt+2)
            with FETCH_LOCK: GLOBAL_COOLDOWN=max(GLOBAL_COOLDOWN,time.monotonic()+wait)
            continue
        status=int(lines[0]);retry_after=lines[1];effective=urllib.parse.urlsplit(lines[2])
        if effective.scheme!='https' or effective.netloc!='lidomarketplus.it': raise FeedError('Unexpected public-feed redirect')
        if status==200:
            try: data=json.loads(body)
            except json.JSONDecodeError as exc: raise FeedError('Invalid JSON body; nothing published')from exc
            if not isinstance(data,dict): raise FeedError('JSON object expected')
            return data
        wait=max(5,2**(attempt+2))
        if retry_after:
            try: wait=max(wait,float(retry_after))
            except ValueError:
                try: wait=max(wait,(parsedate_to_datetime(retry_after)-dt.datetime.now(UTC)).total_seconds())
                except (TypeError,ValueError): pass
        if status not in {408,425,429,500,502,503,504} or attempt==4 or wait>300:
            raise FeedError(f'HTTP {status} {url}; Retry-After={retry_after!r}')
        with FETCH_LOCK: GLOBAL_COOLDOWN=max(GLOBAL_COOLDOWN,time.monotonic()+wait)
        print(f'HTTP {status}; global Retry-After={retry_after!r}; all requests pause at least {wait:.0f}s',flush=True)
    raise FeedError('Unreachable fetch state')

def feed_catalog(locale, fetch=get_json):
    """Read the dedicated public Liquid view, including current variant facts in every page."""
    prefix='' if locale=='it'else'/'+locale
    def read(page):
        obj=fetch(f'{SHOP}{prefix}/collections/all?view=lido-google-feed&sort_by=created-ascending&page={page}&currency=EUR')
        if obj.get('schema')!='lido_public_feed_v1' or obj.get('language')!=locale or obj.get('currency')!='EUR': raise FeedError('Feed locale/currency/schema mismatch')
        if obj.get('page')!=page or obj.get('page_size')!=250 or not isinstance(obj.get('products'),list): raise FeedError('Feed page metadata mismatch')
        return obj
    first=read(1);total=first['total'];pages=first['pages']
    if not isinstance(total,int) or not 0<total<=25000 or pages!=(total+249)//250: raise FeedError('Invalid or unsupported total; never truncate past25000')
    products={}
    def absorb(obj):
        page=obj['page'];expected=min(250,total-(page-1)*250)
        if obj['total']!=total or obj['pages']!=pages or len(obj['products'])!=expected or obj['next_page']!=(page+1 if page<pages else None): raise FeedError('Incomplete or changing feed pagination')
        for product in obj['products']:
            pid=str(product.get('id',''))
            if not pid.isdigit() or pid in products: raise FeedError('Duplicate/missing product in public feed')
            if not isinstance(product.get('variants'),list) or product.get('variants_count')!=len(product['variants']): raise FeedError('Variant pagination incomplete; no truncation allowed')
            products[pid]=product
    absorb(first)
    for start in range(2,pages+1,2):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:
            batch=list(pool.map(read,range(start,min(start+2,pages+1))))
        for obj in batch: absorb(obj)
        if len(products)%2500<500: print(f'{locale} complete public products {len(products)}/{total}',flush=True)
    if len(products)!=total: raise FeedError('Public catalog total mismatch')
    return products,pages

def catalog(locale, fetch=get_json, page_size=250, max_pages=200):
    """Read every successful page; never return partial pagination on errors."""
    prefix='' if locale=='it' else '/'+locale
    products={}; page=1; page_count=0
    while page<=max_pages:
        numbers=list(range(page,min(page+4,max_pages+1)))
        def read(number):
            result=fetch(f'{SHOP}{prefix}/products.json?limit={page_size}&page={number}')
            rows=result.get('products')
            if not isinstance(rows,list) or len(rows)>page_size: raise FeedError('Invalid catalog page')
            return number, rows
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pages=list(pool.map(read,numbers))
        finished=False
        for number,rows in pages:
            if finished:
                if rows: raise FeedError('Products returned after terminal page')
                continue
            page_count+=1
            for p in rows:
                pid=str(p.get('id',''))
                if not pid.isdigit() or pid in products: raise FeedError('Missing/duplicate product across pages')
                products[pid]=p
            if len(rows)<page_size: finished=True
        if finished:
            if not products: raise FeedError('Empty storefront catalog')
            return products,page_count
        page+=len(numbers)
    raise FeedError('Pagination cap reached; refusing partial catalog')

def image_url(raw):
    if not isinstance(raw,str): return ''
    if raw.startswith('//'): raw='https:'+raw
    u=urllib.parse.urlsplit(raw)
    if u.scheme!='https' or not u.netloc or len(raw)>2000: return ''
    # The feed includes only publicly rendered product image URLs, never user uploads or credentials.
    if u.username or u.password: raise FeedError('Credentials in image URL')
    return raw

def current_facts(products, allowlist, exclusions, fetch=get_json):
    """Fetch each authorized public product once for real current barcodes and variant images."""
    denied={str(x)for x in exclusions['retail_product_ids']}|{str(x)for x in exclusions['paused_product_ids']}|{str(x)for x in exclusions.get('policy_hold_product_ids',[])}
    ids=[pid for pid in allowlist['products'] if pid in products and pid not in denied]
    facts={}
    def read(pid):
        handle=allowlist['products'][pid]['handle']
        obj=fetch(f'{SHOP}/products/{urllib.parse.quote(handle,safe="-")}.js')
        if str(obj.get('id'))!=pid or not isinstance(obj.get('variants'),list): raise FeedError('Product facts identity mismatch')
        return pid,obj
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for n,(pid,obj) in enumerate(pool.map(read,ids),1):
            facts[pid]=obj
            if n%500==0: print(f'Fresh public variant facts {n}/{len(ids)}',flush=True)
    return facts

def make_rows(products, allowlist, exclusions, locale, expiry, facts, enabled_groups=None):
    rows=[]; skipped=[]; seen=set(); allowed=allowlist['products']
    policy_holds={str(x)for x in exclusions.get('policy_hold_product_ids',[])}
    denied={str(x) for x in exclusions['retail_product_ids']}|{str(x) for x in exclusions['paused_product_ids']}|policy_holds
    denied_barcodes=set(exclusions.get('paused_barcodes',[]))
    for pid,rule in allowed.items():
        product=products.get(pid)
        if product is None: skipped.append({'id':pid,'reason':'not_public'});continue
        live=facts.get(pid)
        if not live and pid not in denied: raise FeedError('Missing current public product facts')
        tags=product.get('tags',[])
        if isinstance(tags,str): tags=[x.strip()for x in tags.split(',')]
        live_tags=(live or {}).get('tags',[])
        if pid in policy_holds:
            skipped.append({'id':pid,'reason':'unresolved_Google_policy_hold'});continue
        if pid in denied or 'reparto-retail' in tags or 'reparto-retail' in live_tags or BANNED.search(' '.join(tags+live_tags)+' '+product.get('vendor','')):
            skipped.append({'id':pid,'reason':'prohibited'});continue
        if product.get('handle')!=rule['handle']: raise FeedError(f'Unexpected handle change {pid}; re-audit allowlist')
        title=plain(product.get('title','')); description=plain(product.get('body_html') or product.get('description'))
        if not title or not description or CHINESE.search(title+' '+description) or MOQ.search(description):
            skipped.append({'id':pid,'reason':'copy_needs_review'});continue
        images=[image_url(x.get('src',''))for x in product.get('images',[])if isinstance(x,dict)]
        images=[x for x in images if x]
        if not images: skipped.append({'id':pid,'reason':'missing_image'});continue
        for variant in product.get('variants',[]):
            vid=str(variant.get('id','')); v=rule['variants'].get(vid)
            if not v: skipped.append({'id':pid,'variant':vid,'reason':'not_authorized_variant'});continue
            group=v['shipping_group']
            if enabled_groups and group not in enabled_groups: continue
            label={'grossisti':'gsp_grossisti','no_free':'gsp_no_free'}.get(group)
            if not label: raise FeedError(f'Unknown shipping group {group}')
            actual=next((x for x in live['variants']if str(x.get('id'))==vid),None)
            if actual is None: skipped.append({'id':pid,'variant':vid,'reason':'variant_no_longer_public'});continue
            true_gtin=gtin(actual.get('barcode'))
            if actual.get('barcode') in denied_barcodes: continue
            if not isinstance(actual.get('available'),bool): raise FeedError('Missing real availability')
            if not isinstance(actual.get('price'),int) or isinstance(actual.get('price'),bool): raise FeedError('Public .js cents must be an integer')
            try: price=decimal.Decimal(actual['price'])/100
            except (KeyError,decimal.InvalidOperation) as exc: raise FeedError('Invalid public price') from exc
            if not price.is_finite() or price<=0: skipped.append({'id':pid,'variant':vid,'reason':'nonpositive_price'});continue
            oid=f'shopify_ZZ_{pid}_{vid}'
            if oid in seen: raise FeedError('Duplicate variant in generated feed')
            seen.add(oid)
            variant_name=plain(variant.get('title',''))
            suffix='' if variant_name in {'','Default Title','Titolo predefinito'} else ' - '+variant_name
            final_title=(title+suffix)[:150]
            if CHINESE.search(final_title): skipped.append({'id':pid,'variant':vid,'reason':'Chinese_variant'});continue
            prefix='' if locale=='it'else'/'+locale
            feature=actual.get('featured_image')
            variant_image=image_url(feature.get('src','') if isinstance(feature,dict) else feature or '')
            if not variant_image:
                variant_image=next((image_url(x.get('src',''))for x in product.get('images',[])if int(vid)in x.get('variant_ids',[])),images[0] if len(live['variants'])==1 else '')
            if not variant_image:
                skipped.append({'id':pid,'variant':vid,'reason':'missing_exact_variant_image'});continue
            # Unassigned gallery photos may show a different color/packaging; never assume they are generic.
            additional=[image_url(x.get('src',''))for x in product.get('images',[])if int(vid)in x.get('variant_ids',[])]
            row={'id':oid,'title':final_title,'description':description[:5000],
                 'link':f'{SHOP}{prefix}/products/{urllib.parse.quote(rule["handle"],safe="-")}?variant={vid}&currency=EUR',
                 'image_link':variant_image,'additional_image_link':','.join([x for x in additional if x and x!=variant_image][:10]),
                 'availability':'in_stock' if actual['available'] else 'out_of_stock',
                 'price':f'{price:.2f} EUR','condition':'new','gtin':true_gtin,'shipping_label':label,
                 'included_destination':'Free_listings',
                 'excluded_destination':'Shopping_ads,Display_ads,Local_inventory_ads,Free_local_listings,Youtube_affiliate,Youtube_merchandise',
                 'expiration_date':expiry}
            if product.get('vendor','').casefold().replace(' ','')=='bambulab':
                row['brand']='Bambu Lab'
            rows.append(row)
    # Ambiguous duplicate GTINs require source reconciliation; do not invent a preferred product.
    counts={}
    for row in rows:
        if row['gtin']: counts[row['gtin']]=counts.get(row['gtin'],0)+1
    duplicates={x for x,n in counts.items()if n>1}
    skipped.extend({'id':row['id'],'reason':'duplicate_gtin_requires_review','gtin':row['gtin']}for row in rows if row['gtin']in duplicates)
    rows=[row for row in rows if not row['gtin']or row['gtin']not in duplicates]
    rows.sort(key=lambda row:(row['title'].casefold(),row['id']))
    return rows,skipped

def write_atomic(output, by_locale, report, config=None, shipping=None):
    """All locale builds/validation must succeed before replacing any feed."""
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.feed-stage-',dir=output.parent) as temp:
        temp=pathlib.Path(temp)
        for locale,rows in by_locale.items():
            with (temp/(locale+'.tsv')).open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=FIELDS,delimiter='\t',lineterminator='\n');writer.writeheader();writer.writerows(rows)
            raw=(temp/(locale+'.tsv')).read_bytes()
            report['locales'][locale]['sha256']=hashlib.sha256(raw).hexdigest()
            report['locales'][locale]['bytes']=len(raw)
        if config and shipping:
            from xml_feed import write_files
            write_files(temp,by_locale,report,config,shipping)
        (temp/'status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        for file in temp.iterdir(): os.replace(file,output/file.name)

def generate(root=ROOT, output=None, fetch=get_json):
    now=dt.datetime.now(UTC)
    config=json.loads((root/'config.json').read_text());allowlist=json.loads((root/'allowlist.json').read_text());exclusions=json.loads((root/'exclusions.json').read_text())
    verified=dt.datetime.fromisoformat(allowlist['verified_at'])
    if not 0<config['allowlist_max_age_days']<=30 or not 0<config['expiration_hours']<=48: raise FeedError('Unsafe freshness settings')
    if verified.tzinfo is None or verified>now+dt.timedelta(minutes=5) or now-verified>dt.timedelta(days=config['allowlist_max_age_days']): raise FeedError('Publication/GTIN allowlist requires fresh audit')
    locales=config['enabled_locales'];expiry=(now+dt.timedelta(hours=config['expiration_hours'])).strftime('%Y-%m-%dT%H:%MZ')
    report={'generated_at':now.isoformat(),'expires_at':expiry,'allowlist_verified_at':allowlist['verified_at'],'source':'Public localized Shopify JSON; current Google-publication allowlist; verified true barcodes','marketing_method':'Free_listings only','locales':{}}
    built={}
    facts=None
    for locale in locales:
        products,pages=feed_catalog(locale,fetch)
        facts=products
        # Later locale pages may expose a product absent from the earlier language crawl: fail rather than guess.
        rows,skipped=make_rows(products,allowlist,exclusions,locale,expiry,facts,set(config['enabled_shipping_groups']))
        minimum=config.get('minimum_rows',1)
        if len(rows)<minimum: raise FeedError(f'Too few {locale} rows ({len(rows)} < {minimum})')
        nonpublic=sum(x['reason']=='not_public'for x in skipped)
        if nonpublic>max(3,len(allowlist['products'])*config['max_missing_public_fraction']): raise FeedError('Too many authorized products missing from public catalog')
        report['locales'][locale]={'rows':len(rows),'catalog_products':len(products),'pages':pages,'in_stock':sum(r['availability']=='in_stock'for r in rows),'out_of_stock':sum(r['availability']=='out_of_stock'for r in rows),'skipped':skipped,'shipping_labels':sorted({r['shipping_label']for r in rows})}
        built[locale]=rows
        selected=set(config.get('pilot_variant_ids',[]))
        if selected:
            pilot=[r for r in rows if r['id'].rsplit('_',1)[-1]in selected]
            if len(pilot)!=len(selected): raise FeedError('A required pilot representative is absent')
            built[locale+'-pilot']=pilot
            report['locales'][locale+'-pilot']={'rows':len(pilot),'locale':locale,'pilot':True,'in_stock':sum(r['availability']=='in_stock'for r in pilot),'out_of_stock':sum(r['availability']=='out_of_stock'for r in pilot),'shipping_labels':sorted({r['shipping_label']for r in pilot})}
    shipping=json.loads((root/'shipping.json').read_text())
    write_atomic(output or root/'feeds',built,report,config,shipping)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=pathlib.Path);args=parser.parse_args()
    result=generate(output=args.output)
    print(json.dumps({k:v for k,v in result.items()if k!='locales'}))
    for lang,row in result['locales'].items(): print(lang,row['rows'],row['in_stock'],row['out_of_stock'])
