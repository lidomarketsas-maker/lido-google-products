"""Build country-specific Google RSS XML with exact product-level delivery overrides."""
import decimal, gzip, hashlib, json, pathlib, xml.etree.ElementTree as ET
G='http://base.google.com/ns/1.0'
ET.register_namespace('g',G)
SERVICES={'en':'Standard delivery','de':'Standardversand','fr':'Livraison standard','es':'Envío estándar','nl':'Standaardlevering'}

def element(parent,name,value):
    if value is None or value=='':return
    ET.SubElement(parent,'{'+G+'}'+name).text=str(value)

def build_xml(rows,locale,countries,shipping):
    if not countries or len(countries)>25 or len(set(countries))!=len(countries)or'IT'in countries:raise ValueError('New EU source requires1–25 distinct non-IT destinations')
    rss=ET.Element('rss',{'version':'2.0'});channel=ET.SubElement(rss,'channel')
    ET.SubElement(channel,'title').text='Lido Market Plus - '+locale
    ET.SubElement(channel,'link').text='https://lidomarketplus.it/'+locale
    ET.SubElement(channel,'description').text='Public product data'
    for row in rows:
        item=ET.SubElement(channel,'item')
        for name,value in row.items():
            if name in {'additional_image_link','excluded_destination'}:
                for text in value.split(','):
                    if text:element(item,name,text)
            else:element(item,name,value)
        element(item,'ships_from_country','IT')
        element(item,'shipping_handling_business_days',shipping['shipping_handling_business_days'])
        element(item,'shipping_transit_business_days',shipping['shipping_transit_business_days'])
        group={'gsp_grossisti':'grossisti','gsp_no_free':'no_free'}[row['shipping_label']]
        for country in countries:
            rate=shipping['rates'][group][country]
            if decimal.Decimal(rate['price_EUR'])<0:raise ValueError('Negative delivery fee')
            node=ET.SubElement(item,'{'+G+'}shipping')
            for key,value in [('country',country),('service',SERVICES[locale]),('price',rate['price_EUR']+' EUR'),('min_handling_time',rate['min_handling_days']),('max_handling_time',rate['max_handling_days']),('min_transit_time',rate['min_transit_days']),('max_transit_time',rate['max_transit_days'])]:element(node,key,value)
            if group=='grossisti':
                if decimal.Decimal(rate['free_from_EUR'])!=99:raise ValueError('Unexpected wholesale threshold')
                threshold=ET.SubElement(item,'{'+G+'}free_shipping_threshold');element(threshold,'country',country);element(threshold,'price_threshold','99.00 EUR')
            elif rate['free_from_EUR']is not None:raise ValueError('3D/general must never get free shipping')
    xml=ET.tostring(rss,encoding='utf-8',xml_declaration=True)
    parsed=ET.fromstring(xml)
    if len(parsed.findall('./channel/item'))!=len(rows):raise ValueError('XML item count mismatch')
    for item in parsed.findall('./channel/item'):
        if len(item.findall('{'+G+'}shipping'))!=len(countries):raise ValueError('Shipping destination count mismatch')
        expected=len(countries)if item.findtext('{'+G+'}shipping_label')=='gsp_grossisti'else 0
        if len(item.findall('{'+G+'}free_shipping_threshold'))!=expected:raise ValueError('Threshold scope mismatch')
    return xml

def write_files(output,by_locale,report,config,shipping):
    """Called inside the exporter's staging directory, before any public upload."""
    for source,spec in config['sources'].items():
        locale=spec['locale']
        if locale not in by_locale:continue
        for suffix in ['', '-pilot']:
            key=locale+suffix
            if key not in by_locale:continue
            rows=by_locale[key];xml=build_xml(rows,locale,spec['countries'],shipping)
            data=gzip.compress(xml,mtime=0);name=source+suffix+'.xml.gz';(output/name).write_bytes(data)
            report.setdefault('xml_files',{})[name]={'items':len(rows),'locale':locale,'countries':spec['countries'],'feed_label':spec['feed_label'],'gzip_bytes':len(data),'xml_bytes':len(xml),'sha256':hashlib.sha256(data).hexdigest(),'thresholds_per_grossisti_item':len(spec['countries']),'thresholds_per_no_free_item':0}
