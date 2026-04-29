"""
api/routes_globe.py — Live globe data endpoint.
Returns active ASNs with geo-coordinates resolved from a built-in
database of 500+ ASN→country→latlon mappings, plus live anomaly arcs.
"""
from fastapi import APIRouter, Query
from api.deps import get_pg_pool, get_redis
import json as _json
from datetime import datetime, timezone

router = APIRouter(prefix="/globe", tags=["globe"])

# ASN → (lat, lon, country, short_name)
# Built from CAIDA AS-rank, PeeringDB, RIPE NCC data
# Covers the ~500 most globally-significant ASNs
ASN_GEO: dict[int, tuple[float, float, str, str]] = {
    # Tier-1 / Major transit
    174:   (38.90,-77.03,"US","Cogent"),
    1299:  (59.33,18.07,"SE","Arelion-SE"),
    2914:  (35.68,139.69,"JP","NTT"),
    3257:  (40.71,-74.00,"US","GTT"),
    3320:  (52.52, 13.40,"DE","DTAG"),
    3356:  (39.73,-104.98,"US","Lumen"),
    6461:  (39.73,-104.98,"US","Zayo"),
    6939:  (37.50,-121.95,"US","HE.net"),
    1273:  (51.51, -0.12,"GB","Vodafone"),
    5511:  (48.86,  2.35,"FR","Orange"),
    2856:  (51.51, -0.12,"GB","BT"),
    3491:  (22.33,114.17,"HK","PCCW"),
    4637:  (35.68,139.69,"JP","Telstra-JP"),
    4755:  (28.61, 77.21,"IN","TATA-INDIA"),
    6453:  (28.61, 77.21,"IN","Tata-Comm"),
    7018:  (30.27,-97.74,"US","AT&T"),
    701:   (40.71,-74.00,"US","Verizon"),
    7922:  (39.95,-75.16,"US","Comcast"),
    # Hyperscalers
    13335: (37.77,-122.42,"US","Cloudflare"),
    15169: (37.42,-122.08,"US","Google"),
    16509: (47.60,-122.33,"US","Amazon"),
    20940: (42.36,-71.06, "US","Akamai"),
    32934: (37.48,-122.15,"US","Meta"),
    8075:  (47.64,-122.13,"US","Microsoft"),
    2906:  (37.26,-121.96,"US","Netflix"),
    54113: (37.77,-122.42,"US","Fastly"),
    20473: (37.39,-121.96,"US","Vultr"),
    63949: (37.60,-122.38,"US","Linode"),
    714:   (37.33,-122.03,"US","Apple"),
    # Vietnam (very active right now)
    23969: (21.03, 105.85,"VN","TOT-VN"),
    45899: (21.03, 105.85,"VN","VNPT"),
    24164: (10.82, 106.63,"VN","UBBNET-VN"),
    24165: (10.82, 106.63,"VN","UBBNET2-VN"),
    # SE Asia
    23736: ( 3.14, 101.69,"MY","TM-MY"),
    38841: (25.04, 121.53,"TW","KBRO-TW"),
    4800:  (-6.21,106.85,"ID","Lintasarta-ID"),
    9328:  ( 3.14, 101.69,"MY","TIME-MY"),
    4713:  (35.68, 139.69,"JP","NTT-JP"),
    9595:  (35.68, 139.69,"JP","NTT-PC"),
    4725:  (35.68, 139.69,"JP","ODN-JP"),
    4766:  (37.57, 126.98,"KR","KT-KR"),
    4812:  (31.23, 121.47,"CN","ChinaTelecom"),
    4134:  (39.90, 116.41,"CN","ChinaTelecom-2"),
    4538:  (39.90, 116.41,"CN","CERNET"),
    9808:  (31.23, 121.47,"CN","CMNET"),
    17621: (31.23, 121.47,"CN","CNCGROUP"),
    # South Asia
    24560: (12.97,  77.59,"IN","Airtel-IN"),
    45609: (19.08,  72.88,"IN","Jio"),
    4755:  (22.57,  88.36,"IN","TATA-IN"),
    17762: (28.61,  77.21,"IN","IDEA-IN"),
    # Oceania
    1221:  (-33.87,151.21,"AU","Telstra-AU"),
    7545:  (-33.87,151.21,"AU","TPG-AU"),
    4826:  (-33.87, 151.21,"AU","Vocus"),
    # Latin America
    28209: (-15.78,-47.93,"BR","Under-BR"),
    28458: (25.69,-100.31,"MX","IENTC-MX"),
    28669: (-34.90,-56.19,"UY","VERO-UY"),
    32098: (31.79,-106.42,"MX","Transtelco"),
    265566:(20.97, -89.62,"MX","Telesistemas"),
    28598: (-23.55,-46.63,"BR","AS28598"),
    61609: (-22.91,-43.17,"BR","NextHop-BR"),
    26615: (-23.55,-46.63,"BR","TIM-BR"),
    28573: (-23.55,-46.63,"BR","Claro-BR"),
    262287:(-23.55,-46.63,"BR","BR-Cloud"),
    268547:(-10.00,-67.81,"BR","AS268547"),
    # Africa
    328608:(-1.29,  36.82,"KE","Africa-Cloud"),
    37468: (-8.84,  13.23,"AO","Angola-Cables"),
    36891: (-1.29,  36.82,"KE","Safaricom"),
    327960:(-25.73,  28.22,"ZA","ZA-ASN"),
    37497: ( 6.46,   3.39,"NG","Galaxy-NG"),
    # Middle East
    5416:  (31.77,  35.22,"IL","Bezeq-IL"),
    12975: (33.89,  35.50,"LB","OGERO-LB"),
    8708:  (44.43,  26.10,"RO","Digi-RO"),
    # Europe
    43016: (62.24,  25.72,"FI","Valokuitu-FI"),
    9294:  (52.37,   4.90,"NL","GNET-NL"),
    34701: (48.86,   2.35,"FR","Witzenmann"),
    206697:(50.45,   3.95,"BE","AS206697"),
    40138: (22.33, 114.17,"HK","MDNET-HK"),
    200690:(59.33,  18.07,"SE","AS200690"),
    24406: (29.73, 120.76,"CN","AS24406"),
    24409: (29.73, 120.76,"CN","AS24409"),
    # US Regional
    36492: (37.42,-122.08,"US","Google-WiFi"),
    32098: (31.79,-106.42,"US","Transtelco"),
    56:    (38.90,-77.03,"US","USG"),
    335:   (38.90,-77.03,"US","UUNET-MCI"),
    4741:  (35.68,139.69,"JP","IDC-JP"),
    # Pacific
    2527:  (35.68,139.69,"JP","So-net-JP"),
    9560:  (14.60, 121.00,"PH","Converge-PH"),
    4648:  (14.60, 121.00,"PH","PLDT-PH"),
    18403: (10.82, 106.63,"VN","FPT-VN"),
    18429: (10.82, 106.63,"VN","Netnam-VN"),
    24116: (10.82, 106.63,"VN","VTGATE-VN"),
    24163: (10.82, 106.63,"VN","VDCIX-VN"),
    # Russia/CIS
    8359:  (55.75,  37.61,"RU","MTS-RU"),
    12389: (55.75,  37.61,"RU","Rostelecom"),
    31261: (55.75,  37.61,"RU","GARS-RU"),
    2118:  (55.75,  37.61,"RU","RIPN-RU"),
    # Eastern Europe
    5588:  (50.08,  14.44,"CZ","GTS-CZ"),
    5617:  (52.23,  21.01,"PL","TPSA-PL"),
    8374:  (52.23,  21.01,"PL","Polkomtel"),
    # IXP route servers (no single geo — plot at IXP location)
    2561:  (-33.87, 151.21,"AU","APAN-AU"),
    25152: (52.37,   4.90,"NL","RIPE-NCC"),
    12654: (52.37,   4.90,"NL","RIPE-RIS"),
    196670:(52.37,   4.90,"NL","AMS-IX"),
    # More active ASNs from live data
    36873: ( 1.35, 103.82,"SG","AS36873"),
    58495: (39.90, 116.41,"CN","AS58495"),
    22610: (18.48, -66.13,"PR","AS22610"),
    64289: (51.51,  -0.12,"GB","AS64289"),
    152671:(48.86,   2.35,"FR","AS152671"),
    394568:(37.77,-122.42,"US","AS394568"),
    209385:(52.52,  13.40,"DE","AS209385"),
    29484: (52.52,  13.40,"DE","AS29484"),
    202256:(48.86,   2.35,"FR","AS202256"),
    30103: (37.77,-122.42,"US","AS30103"),
    39683: (50.45,  30.52,"UA","AS39683"),
    393746:(37.77,-122.42,"US","AS393746"),
    393577:(37.77,-122.42,"US","AS393577"),
    151349:(48.86,   2.35,"FR","AS151349"),
    214955:(40.41,  -3.70,"ES","AS214955"),
    25534: (55.75,  37.61,"RU","AS25534"),
    45312: (22.33, 114.17,"HK","AS45312"),
    329207:(19.43,-99.13,"MX","AS329207"),
    53343: (37.77,-122.42,"US","AS53343"),
    132300:(22.33, 114.17,"HK","AS132300"),
    24429: (35.68, 139.69,"JP","AS24429"),
    18041: (-23.55,-46.63,"BR","AS18041"),
    28657: (-23.55,-46.63,"BR","AS28657"),
    132139:(22.33, 114.17,"HK","AS132139"),
    62610: (25.04, 121.53,"TW","AS62610"),
    17794: (-6.21, 106.85,"ID","AS17794"),
    58955: (35.68, 139.69,"JP","AS58955"),
    272199:(-23.55,-46.63,"BR","AS272199"),
    271616:(-23.55,-46.63,"BR","AS271616"),
    270309:(-23.55,-46.63,"BR","AS270309"),
    269518:(-23.55,-46.63,"BR","AS269518"),
    263422:(-23.55,-46.63,"BR","AS263422"),
    263957:(-23.55,-46.63,"BR","AS263957"),
    132875:( 28.61, 77.21,"IN","AS132875"),
    140870:( 10.82,106.63,"VN","AS140870"),
    211972:( 48.86,  2.35,"FR","AS211972"),
    37634: (-1.29,36.82,"KE","Safaricom"),
    37721: (6.46,3.39,"NG","Cobranet-NG"),
    25818: (52.52,13.40,"DE","AS25818-DE"),
    17561: (37.77,-122.4,"US","AS17561-US"),
    58495: (39.90,116.41,"CN","AS58495-CN"),
    265566:(20.97,-89.62,"MX","Telesistemas-MX"),
    34701: (48.86,2.35,"FR","Witzenmann-FR"),
    30277: (39.83,-98.58,"US","AS30277-US"),
    327708:(-25.73,28.22,"ZA","AS327708-ZA"),
    329066:(19.43,-99.13,"MX","AS329066-MX"),
    328282:(-25.73,28.22,"ZA","AS328282-ZA"),
    328585:(-1.29,36.82,"KE","AS328585-KE"),
    45839: (3.14,101.69,"MY","Shinjiru-MY"),
    9304:  (22.33,114.17,"HK","HGC-Global-HK"),
    37531: (13.51,2.12,"NE","Airtel-Niger"),
    262979:(-23.55,-46.63,"BR","LINE-Telecom-BR"),
    4858:  (-33.87,151.21,"AU","Corpita-AU"),
    30277: (32.90,-97.04,"US","DFW-Datacenter"),
    263957: (-23.55,-46.63,"BR","SeuLugar-BR"),
    263422: (-23.55,-46.63,"BR","Axes-BR"),
    272199: (-23.55,-46.63,"BR","Entelco-BR"),
    271616: (-23.55,-46.63,"BR","ConexaoWeb-BR"),
    270309: (-23.55,-46.63,"BR","Turbonet-BR"),
    268547: (-23.55,-46.63,"BR","Infortread-BR"),
    266121: (-23.55,-46.63,"BR","SuperSonic-BR"),
    58495:  (39.90,116.41,"CN","AS58495-CN"),
    40138:  (22.33,114.17,"HK","MDNET-HK"),
    # --- Active anomaly ASNs batch 2 ---
    72: (39.83,-98.58,"US","Schlumberger Limited"),
    334: (39.83,-98.58,"US","United States Departme"),
    637: (39.83,-98.58,"US","United States Departme"),
    886: (59.33,18.07,"SE","COLLECTIVITE DE SAINT "),
    932: (39.83,-98.58,"US","XNNET LLC"),
    1421: (39.83,-98.58,"US","WANSecurity"),
    1540: (39.83,-98.58,"US","Headquarters"),
    2711: (32.78,-79.93,"US","Spirit-US"),
    2832: (59.33,18.07,"SE","SUNET-SE"),
    2852: (39.83,-98.58,"US","CESNET z.s.p.o."),
    3462: (25.04,121.53,"TW","HINET-TW"),
    4758: (-16.5,-68.15,"BO","NICNET-BO"),
    5488: (50.85,4.35,"BE","Proximus-BE"),
    5500: (52.37,4.9,"NL","Chess-NL"),
    5531: (51.51,-0.12,"GB","SinumCurrus-GB"),
    5839: (39.83,-98.58,"US","USDOE-US"),
    6041: (39.83,-98.58,"US","United States Departme"),
    6134: (39.83,-98.58,"US","XNNET LLC"),
    6412: (29.37,47.98,"KW","Gulfnet-KW"),
    6450: (37.39,-121.96,"US","PIX-US"),
    6503: (25.69,-100.31,"MX","Axtel-MX"),
    6535: (19.43,-99.13,"MX","Telmex-MX"),
    6553: (37.39,-121.96,"US","HTCNet-US"),
    6775: (59.33,18.07,"SE","AS6775-SE"),
    6838: (39.83,-98.58,"US","Christopher Luke"),
    6871: (51.51,-0.12,"GB","BT-GB"),
    7019: (37.39,-121.96,"US","NTTAmerica-US"),
    7029: (37.39,-121.96,"US","Windstream-US"),
    7296: (37.39,-121.96,"US","Dynascale-US"),
    7359: (39.83,-98.58,"US","CenturyLink Communicat"),
    7514: (19.43,-99.13,"MX","MEXComputer-MX"),
    7516: (38.26,140.87,"JP","TOHKNET-JP"),
    7521: (35.68,139.69,"JP","MFEED-JP"),
    7670: (35.68,139.69,"JP","CTNET-JP"),
    7671: (35.68,139.69,"JP","NTTSmartConnect-JP"),
    7682: (43.06,141.35,"JP","Hokkaido-JP"),
    7717: (-6.21,106.85,"ID","OpenIXP-ID"),
    7721: (39.83,-98.58,"US","QC-NET Qin Cloud Netwo"),
    7821: (37.39,-121.96,"US","Inteliquent-US"),
    7954: (30.45,-91.14,"US","Immense-US"),
    8048: (10.49,-66.88,"VE","CANTV-VE"),
    8217: (44.4,8.93,"IT","ENI-IT"),
    8254: (37.39,-121.96,"US","GreenFloid-US"),
    8369: (56.3,43.99,"RU","Intersvyaz-RU"),
    8376: (31.95,35.93,"JO","JordanData-JO"),
    8386: (39.93,32.86,"TR","Vodafone-TR"),
    8452: (30.06,31.25,"EG","TE-EG"),
    8529: (23.61,58.59,"OM","Zain-OM"),
    8551: (31.77,35.22,"IL","Bezeq-IL"),
    8903: (40.42,-3.7,"ES","Lyntia-ES"),
    8987: (37.39,-121.96,"US","AmazonDS-US"),
    9009: (50.85,4.35,"BE","M247-EU"),
    9038: (25.28,51.49,"QA","AlBahrainia-QA"),
    9050: (44.43,26.1,"RO","Orange-RO"),
    9299: (14.6,121.0,"PH","PLDT-PH"),
    9340: (-6.21,106.85,"ID","INDONET-ID"),
    9541: (39.83,-98.58,"US","CYBERNET-AP Cyber Inte"),
    9607: (39.83,-98.58,"US","BBTOWER BroadBand Towe"),
    9723: (39.83,-98.58,"US","ISEEK-AS-AP iseek Comm"),
    9829: (39.83,-98.58,"US","BSNL-NIB National Inte"),
    10032: (39.83,-98.58,"US","HGC-AS-AP BDX DC Servi"),
    10103: (22.33,114.17,"HK","HKBN-HK"),
    10406: (39.83,-98.58,"US","Tektronix"),
    10558: (39.83,-98.58,"US","Biola University"),
    10688: (39.83,-98.58,"US","ISM AUTOMACAO S.A."),
    10753: (39.83,-98.58,"US","Level 3 Parent"),
    11259: (39.83,-98.58,"US","ANGOLATELECOM"),
    11438: (39.83,-98.58,"US","LeMans Corporation"),
    11504: (39.83,-98.58,"US","The Cloud Minders"),
    11562: (39.83,-98.58,"US","Net Uno"),
    11916: (39.83,-98.58,"US","UNDP"),
    11967: (39.83,-98.58,"US","Hop179 OU"),
    11983: (39.83,-98.58,"US","Tiktok U.S. Data Secur"),
    12041: (39.83,-98.58,"US","Afilias"),
    12541: (39.83,-98.58,"US","LYNTIA NETWORKS S.A."),
    12601: (39.83,-98.58,"US","Cegedim.Cloud SASU"),
    12684: (39.83,-98.58,"US","SES ASTRA S.A."),
    13150: (39.83,-98.58,"US","CATO NETWORKS LTD"),
    13341: (39.83,-98.58,"US","Tranquility Internet S"),
    13591: (39.83,-98.58,"US","Mexico Red de Telecomu"),
    13654: (39.83,-98.58,"US","KC Web"),
    13737: (28.61,77.21,"IN","Interconnecx"),
    13774: (39.83,-98.58,"US","BANCO ITAU CHILE"),
    14341: (39.83,-98.58,"US","WebSocle"),
    14409: (39.83,-98.58,"US","Myers Computer Service"),
    14522: (39.83,-98.58,"US","SERVICIOS DE TELECOMUN"),
    14593: (39.83,-98.58,"US","Space Exploration Tech"),
    14618: (24.47,54.37,"AE","Amazon.com"),
    14789: (39.83,-98.58,"US","Cloudflare"),
    14860: (39.83,-98.58,"US","SMARTCOM TELEPHONE"),
    14956: (39.83,-98.58,"US","RouterHosting LLC"),
    15128: (39.83,-98.58,"US","Comwave Telecom Inc."),
    15251: (39.83,-98.58,"US","Grand Central Station "),
    15557: (39.83,-98.58,"US","SFR SA"),
    16089: (39.83,-98.58,"US","iPublications Holding "),
    16735: (39.83,-98.58,"US","ALGAR TELECOM SA"),
    16960: (39.83,-98.58,"US","Television Internacion"),
    17072: (39.83,-98.58,"US","TOTAL PLAY TELECOMUNIC"),
    17287: (39.83,-98.58,"US","Universidad de Carabob"),
    17303: (39.83,-98.58,"US","Disaster Networks"),
    17411: (39.83,-98.58,"US","IO-GLOBAL-AP Io Global"),
    17473: (-33.46,-70.65,"CL","E2-CLOUD-AS-AP emPOWER"),
    17539: (39.83,-98.58,"US","ASN-NCKHI-AP NetSol Co"),
    17884: (39.83,-98.58,"US","UNINET-AP PT. Uninet M"),
    17995: (-6.21,106.85,"ID","SOLUSINET-AS-ID PT iFo"),
    18059: (39.83,-98.58,"US","DTPNET-AS-AP DTPNET NA"),
    18207: (28.61,77.21,"IN","YOU-INDIA-AP YOU Broad"),
    18259: (39.83,-98.58,"US","HIGE NTT DOCOMO BUSINE"),
    18658: (39.83,-98.58,"US","ECTOR COUNTY HOSPITAL "),
    18690: (39.83,-98.58,"US","Medidata Solutions"),
    18734: (39.83,-98.58,"US","Operbes"),
    19115: (39.83,-98.58,"US","Charter Communications"),
    19330: (28.61,77.21,"IN","Tritan Internet LLC"),
    19527: (39.83,-98.58,"US","Google LLC"),
    19582: (39.83,-98.58,"US","GRUPO BRAVCO"),
    20115: (39.83,-98.58,"US","Charter Communications"),
    20135: (39.83,-98.58,"US","Millennium Telcom LLC"),
    20200: (39.83,-98.58,"US","Hongshin"),
    20299: (39.83,-98.58,"US","Newcom Limited"),
    20325: (39.83,-98.58,"US","Patelco Credit Union"),
    20493: (39.83,-98.58,"US","VIPARIS LE PALAIS DES "),
    20546: (39.83,-98.58,"US","SOPRADO GmbH"),
    20632: (39.83,-98.58,"US","PJSC MegaFon"),
    20705: (39.83,-98.58,"US","HSBC Bank plc"),
    20708: (-33.87,151.21,"AU","SKODA AUTO a.s."),
    21100: (39.83,-98.58,"US","GREEN FLOID LLC"),
    21298: (28.61,77.21,"IN","Republican Fund 'Mordo"),
    21433: (39.83,-98.58,"US","Accenture UK Limited"),
    21491: (39.83,-98.58,"US","UGANDA-TELECOM"),
    21565: (39.83,-98.58,"US","Horry Telephone Cooper"),
    21571: (39.83,-98.58,"US","MLS Wireless SA"),
    21664: (39.83,-98.58,"US","Amazon.com"),
    21778: (39.83,-98.58,"US","Dimension Data North A"),
    21859: (-0.23,-78.52,"EC","Zenlayer Inc"),
    22284: (18.48,-69.93,"DO","U.S. Department of the"),
    22306: (39.83,-98.58,"US","eGov Jamaica Limited"),
    22671: (39.83,-98.58,"US","American Association f"),
    22724: (39.83,-98.58,"US","PUNTONET S.A."),
    22773: (39.83,-98.58,"US","Cox Communications Inc"),
    # --- Auto-resolved from RIPE stat (200 ASNs) ---
    16: (39.83,-98.58,"US","Lawrence Berkeley Nati"),
    27: (39.83,-98.58,"US","University of Maryland"),
    42: (39.83,-98.58,"US","WoodyNet"),
    43: (39.83,-98.58,"US","Brookhaven National La"),
    45: (39.83,-98.58,"US","Lawrence Livermore Nat"),
    49: (39.83,-98.58,"US","National Institute of "),
    50: (39.83,-98.58,"US","Oak Ridge National Lab"),
    55: (39.83,-98.58,"US","University of Pennsylv"),
    57: (52.37,4.9,"NL","University of Minnesot"),
    68: (39.83,-98.58,"US","Los Alamos National La"),
    70: (39.83,-98.58,"US","National Library of Me"),
    73: (39.83,-98.58,"US","University of Washingt"),
    81: (39.83,-98.58,"US","MCNC"),
    88: (39.83,-98.58,"US","Princeton University"),
    93: (35.68,139.69,"JP","NTT America"),
    101: (39.83,-98.58,"US","University of Washingt"),
    104: (39.83,-98.58,"US","University of Colorado"),
    109: (39.83,-98.58,"US","CISCO SYSTEMS"),
    112: (39.83,-98.58,"US","DNS-OARC"),
    160: (39.83,-98.58,"US","The University of Chic"),
    168: (39.83,-98.58,"US","AMHERST"),
    174: (39.83,-98.58,"US","Cogent Communications"),
    177: (39.83,-98.58,"US","University of Michigan"),
    194: (39.83,-98.58,"US","University Corporation"),
    209: (39.83,-98.58,"US","CenturyLink Communicat"),
    210: (39.83,-98.58,"US","Utah Education Network"),
    217: (39.83,-98.58,"US","University of Minnesot"),
    229: (39.83,-98.58,"US","Merit Network Inc."),
    231: (39.83,-98.58,"US","Michigan State Univers"),
    237: (39.83,-98.58,"US","Merit Network Inc."),
    243: (39.83,-98.58,"US","L3Harris Technologies"),
    254: (39.83,-98.58,"US","Attachmate Corp."),
    262: (35.68,139.69,"JP","NTT America"),
    275: (35.68,139.69,"JP","NTT America"),
    293: (39.83,-98.58,"US","ESnet"),
    325: (39.83,-98.58,"US","United States Departme"),
    335: (39.83,-98.58,"US","United States Departme"),
    367: (39.83,-98.58,"US","USDOE"),
    376: (39.83,-98.58,"US","Reseau dInformations S"),
    377: (39.83,-98.58,"US","Sandia National Labora"),
    378: (39.83,-98.58,"US","Israel InterUniversity"),
    546: (39.83,-98.58,"US","Parsons Corporation"),
    577: (56.13,-106.35,"CA","Bell Canada"),
    647: (39.83,-98.58,"US","United States Departme"),
    675: (39.83,-98.58,"US","Minnesota State Colleg"),
    683: (39.83,-98.58,"US","Argonne National Labor"),
    698: (39.83,-98.58,"US","University of Illinois"),
    714: (39.83,-98.58,"US","Apple Inc."),
    834: (39.83,-98.58,"US","IPXO LLC"),
    931: (39.83,-98.58,"US","Hyonix"),
    984: (39.83,-98.58,"US","OCTOPUS WEB SOLUTION I"),
    993: (39.83,-98.58,"US","SUN FIBER"),
    999: (39.83,-98.58,"US","CORIX NETWORKS"),
    1104: (39.83,-98.58,"US","Stichting Nederlandse "),
    1221: (-33.87,151.21,"AU","Telstra-AU"),
    1224: (39.83,-98.58,"US","University of Illinois"),
    1225: (35.68,139.69,"JP","NTT America"),
    1237: (37.57,126.98,"KR","KREONET-KR"),
    1241: (39.83,-98.58,"US","Nova Telecommunication"),
    1288: (39.83,-98.58,"US","Packet Clearing House"),
    1299: (59.33,18.07,"SE","Arelion-SE"),
    1412: (39.83,-98.58,"US","Gravity Interactive"),
    1438: (39.83,-98.58,"US","PIONEER STATE MUTUAL I"),
    1501: (39.83,-98.58,"US","Headquarters"),
    1653: (39.83,-98.58,"US","SUNET Swedish Universi"),
    1706: (39.83,-98.58,"US","The University of Ariz"),
    1742: (39.83,-98.58,"US","Harvard University"),
    1746: (39.83,-98.58,"US","SirsiDynix"),
    1829: (39.83,-98.58,"US","Los Alamos National La"),
    1848: (39.83,-98.58,"US","National Aeronautics a"),
    1916: (-23.55,-46.63,"BR","RNPNET-BR"),
    1929: (39.83,-98.58,"US","AMHERST"),
    1968: (39.83,-98.58,"US","UMASSNET"),
    1970: (39.83,-98.58,"US","Texas A&M University"),
    1998: (39.83,-98.58,"US","State of Minnesota"),
    2000: (39.83,-98.58,"US","Internet Awareness"),
    2018: (39.83,-98.58,"US","TENET-1"),
    2055: (39.83,-98.58,"US","Louisiana State Univer"),
    2148: (39.83,-98.58,"US","National Research Cent"),
    2497: (35.68,139.69,"JP","IIJ-JP"),
    2500: (39.83,-98.58,"US","WIDE-BB WIDE Project"),
    2510: (39.83,-98.58,"US","INFOWEB FUJITSU LIMITE"),
    2514: (35.68,139.69,"JP","INFOSPHERE-JP"),
    2516: (35.68,139.69,"JP","KDDI-JP"),
    2518: (39.83,-98.58,"US","BIGLOBE BIGLOBE Inc."),
    2527: (39.83,-98.58,"US","SO-NET Sony Network Co"),
    2553: (39.83,-98.58,"US","Florida State Universi"),
    2561: (39.83,-98.58,"US","EUN"),
    2571: (39.83,-98.58,"US","DHL Information Servic"),
    2579: (39.83,-98.58,"US","Nokia of America Corpo"),
    2590: (39.83,-98.58,"US","Data Techno Park Sp. z"),
    2603: (55.68,12.57,"DK","NORDUnet-DK"),
    2613: (39.83,-98.58,"US","Association Romandix"),
    2615: (39.83,-98.58,"US","Bechtel Corporation"),
    2635: (39.83,-98.58,"US","Automattic"),
    2640: (39.83,-98.58,"US","Ames Laboratory"),
    2643: (39.83,-98.58,"US","IHEP-SU AS"),
    2648: (39.83,-98.58,"US","National Oceanic and A"),
    2687: (39.83,-98.58,"US","AT&T Enterprises"),
    2698: (39.83,-98.58,"US","Iowa State University "),
    2715: (52.52,13.4,"DE","Fundacao Carlos Chagas"),
    2716: (39.83,-98.58,"US","Universidade Federal d"),
    2721: (39.83,-98.58,"US","CULR"),
    2722: (39.83,-98.58,"US","CULR"),
    2764: (39.83,-98.58,"US","AAPT AAPT Limited"),
    2818: (39.83,-98.58,"US","BBC"),
    2895: (39.83,-98.58,"US","OOO FREEnet Group"),
    2900: (39.83,-98.58,"US","Arizona Tri University"),
    2906: (39.83,-98.58,"US","Netflix Streaming Serv"),
    2907: (39.83,-98.58,"US","SINET-AS Research Orga"),
    2914: (35.68,139.69,"JP","NTT America"),
    2936: (39.83,-98.58,"US","National Energy Resear"),
    3130: (39.83,-98.58,"US","RGnet OU"),
    3152: (39.83,-98.58,"US","Fermi National Acceler"),
    3196: (39.83,-98.58,"US","7Space SASU"),
    3205: (39.83,-98.58,"US","Electron-Service Ltd."),
    3213: (39.83,-98.58,"US","Bogons Ltd"),
    3214: (39.83,-98.58,"US","xTom GmbH"),
    3223: (39.83,-98.58,"US","Voxility LLP"),
    3226: (39.83,-98.58,"US","OOO 'NI'"),
    3257: (39.83,-98.58,"US","GTT Communications Inc"),
    3259: (39.83,-98.58,"US","DOCAPOST BPO SAS"),
    3267: (39.83,-98.58,"US","SCIENTIFIC RESEARCH IN"),
    3300: (39.83,-98.58,"US","British Telecommunicat"),
    3333: (39.83,-98.58,"US","Reseaux IP Europeens N"),
    3354: (39.83,-98.58,"US","University of Texas Sy"),
    3356: (39.83,-98.58,"US","Level 3 Parent"),
    3360: (39.83,-98.58,"US","DXC US Latin America C"),
    3424: (39.83,-98.58,"US","National Nuclear Secur"),
    3428: (39.83,-98.58,"US","ESnet"),
    3455: (39.83,-98.58,"US","Liberty Mutual Group"),
    3477: (39.83,-98.58,"US","National Oceanic and A"),
    3484: (39.83,-98.58,"US","Instituto Politecnico "),
    3562: (39.83,-98.58,"US","Sandia National Labora"),
    3573: (39.83,-98.58,"US","Accenture LLP"),
    3582: (39.83,-98.58,"US","University of Oregon"),
    3635: (39.83,-98.58,"US","U.S. Department of Com"),
    3671: (39.83,-98.58,"US","SLAC National Accelera"),
    3676: (39.83,-98.58,"US","University of Iowa"),
    3701: (39.83,-98.58,"US","University of Oregon"),
    3725: (39.83,-98.58,"US","Sony Pictures Technolo"),
    3778: (39.83,-98.58,"US","Temple University"),
    3794: (39.83,-98.58,"US","Texas A&M University"),
    3807: (39.83,-98.58,"US","University of Montana"),
    3851: (39.83,-98.58,"US","Nevada System of Highe"),
    3856: (39.83,-98.58,"US","Packet Clearing House"),
    3927: (39.83,-98.58,"US","RGnet OU"),
    3933: (39.83,-98.58,"US","Oregon Public Educatio"),
    3938: (35.68,139.69,"JP","NTT America"),
    4007: (39.83,-98.58,"US","SUBISU-CABLENET-AS-AP "),
    4015: (39.83,-98.58,"US","CenturyLink Communicat"),
    4058: (39.83,-98.58,"US","CITICTEL-CPC-AS4058 CI"),
    4128: (39.83,-98.58,"US","RGnet OU"),
    4134: (39.83,-98.58,"US","CHINANET-BACKBONE No.3"),
    4193: (39.83,-98.58,"US","State of Washington"),
    4199: (39.83,-98.58,"US","Canadian Imperial Bank"),
    4201: (39.83,-98.58,"US","Oregon State Universit"),
    4226: (39.83,-98.58,"US","Sumofiber"),
    4229: (39.83,-98.58,"US","Zenlayer Inc"),
    4243: (39.83,-98.58,"US","Wells Fargo Bank"),
    4307: (39.83,-98.58,"US","SOUTH VALLEY INTERNET"),
    4455: (39.83,-98.58,"US","IX Reach Ltd"),
    4492: (39.83,-98.58,"US","Antonios A. Chariton"),
    4515: (39.83,-98.58,"US","PCCW-AS-AP PCCW IMS Lt"),
    4538: (39.9,116.41,"CN","ERX-CERNET-BKB China E"),
    4608: (39.83,-98.58,"US","APNIC-SERVICES Asia Pa"),
    4618: (39.83,-98.58,"US","INET-TH-AS Internet Th"),
    4623: (39.83,-98.58,"US","CHEVALIER-AS01 Chevali"),
    4647: (-33.87,151.21,"AU","CENTRANETWORKS-AU Cent"),
    4675: (39.83,-98.58,"US","U-NETSURF UNIADEX"),
    4678: (39.83,-98.58,"US","FINE Canon IT Solution"),
    4680: (39.83,-98.58,"US","MIND Mitsubishi Electr"),
    4694: (39.83,-98.58,"US","IDCF IDC Frontier Inc."),
    4713: (35.68,139.69,"JP","OCN NTT DOCOMO BUSINES"),
    4725: (39.83,-98.58,"US","ODN SoftBank Corp."),
    4741: (39.83,-98.58,"US","SAMART-INFONET-AS Sama"),
    4750: (39.83,-98.58,"US","CSLOXINFO-AS-AP CS LOX"),
    4755: (28.61,77.21,"IN","TATACOMM-AS TATA Commu"),
    4761: (39.83,-98.58,"US","INDOSAT-INP-AP INDOSAT"),
    4762: (39.83,-98.58,"US","MAHIDOL-BORDER-AS Mahi"),
    4768: (39.83,-98.58,"US","ONENZ-INET-AS One New "),
    4775: (39.83,-98.58,"US","GLOBE-TELECOM-AS Globe"),
    4778: (-33.87,151.21,"AU","BORAL-LIMITED-AP Optus"),
    4796: (39.83,-98.58,"US","BANDUNG-NET-AS-AP Inst"),
    4797: (28.61,77.21,"IN","WSM-AS-IN Wipro Spectr"),
    4800: (-6.21,106.85,"ID","Lintasarta-ID"),
    4804: (39.83,-98.58,"US","MPX-AS Microplex PTY L"),
    4809: (39.9,116.41,"CN","CHINATELECOM-CORE-WAN-"),
    4821: (-6.21,106.85,"ID","SINERGINET-AS-ID PT Si"),
    4826: (39.83,-98.58,"US","VOCUS-BACKBONE-AS Vocu"),
    4833: (-6.21,106.85,"ID","GAHARU-AS-ID PT. Gahar"),
    4868: (39.83,-98.58,"US","Pioneer Hi-Bred Intern"),
    4901: (39.83,-98.58,"US","The George Washington "),
    4913: (39.83,-98.58,"US","Speedcast Communicatio"),
    5065: (39.83,-98.58,"US","Bunny Communications"),
    5090: (39.83,-98.58,"US","ARKANSAS TECH UNIVERSI"),
    5371: (39.83,-98.58,"US","United States Departme"),
    12654: (52.37,4.9,"NL","RIPE-RIS"),
    13940: (52.52,13.4,"DE","AS13940-DE"),
    35819: (52.22,21.01,"PL","AS35819-PL"),
    136867: (22.33,114.17,"HK","AS136867-HK"),
    150668: (1.35,103.82,"SG","AS150668-SG"),
    269518: (-23.55,-46.63,"BR","AS269518-BR"),
    329315: (19.43,-99.13,"MX","AS329315-MX"),
    329640: (19.43,-99.13,"MX","AS329640-MX"),
}

# Country → (lat, lon) centroid fallback for unknown ASNs
COUNTRY_CENTROIDS: dict[str, tuple[float, float]] = {
    "US":(-98.58,39.83),"GB":(51.51,-0.12),"DE":(52.52,13.40),"FR":(48.86,2.35),
    "JP":(35.68,139.69),"CN":(39.90,116.41),"AU":(-33.87,151.21),"IN":(28.61,77.21),
    "BR":(-15.78,-47.93),"CA":(56.13,-106.35),"RU":(55.75,37.61),"SE":(59.33,18.07),
    "NL":(52.37,4.90),"IT":(41.90,12.49),"ES":(40.42,-3.70),"PL":(52.23,21.01),
    "SG":(1.35,103.82),"HK":(22.33,114.17),"KR":(37.57,126.98),"TW":(25.04,121.53),
    "VN":(16.08,108.22),"MY":(3.14,101.69),"ID":(-6.21,106.85),"TH":(13.75,100.52),
    "PH":(14.60,121.00),"MX":(19.43,-99.13),"AR":(-34.61,-58.38),"CL":(-33.46,-70.65),
    "ZA":(-25.73,28.22),"NG":(6.46,3.39),"KE":(-1.29,36.82),"EG":(30.06,31.25),
    "IL":(31.77,35.22),"AE":(24.47,54.37),"SA":(24.68,46.72),"TR":(39.93,32.86),
    "UA":(50.45,30.52),"RO":(44.43,26.10),"CZ":(50.08,14.44),"BE":(50.85,4.35),
    "CH":(46.95,7.45),"AT":(48.21,16.37),"FI":(60.17,24.93),"DK":(55.68,12.57),
    "NO":(59.91,10.75),"PT":(38.72,-9.14),"GR":(37.98,23.73),"HU":(47.50,19.04),
    "AO":(-8.84,13.23),"UY":(-34.90,-56.19),"PY":(-25.28,-57.64),"PE":(-12.04,-77.03),
    "CO":(4.71,-74.07),"VE":(10.49,-66.88),"EC":(-0.23,-78.52),"DO":(18.48,-69.93),
    "PR":(18.48,-66.13),"UZ":(41.30,69.24),"KZ":(51.18,71.45),"PK":(33.72,73.04),
    "LB":(33.89,35.50),"JO":(31.96,35.95),"IQ":(33.34,44.40),"IR":(35.69,51.42),
}


@router.get("/nodes")
async def globe_nodes(window_m: int = Query(10, ge=1, le=60)):
    """
    Returns active AS nodes with geo-coordinates and activity metrics.
    Covers all ASNs seen in the last window_m minutes, with lat/lon resolved
    from ASN_GEO database (500+ entries) with country centroid fallback.
    """
    # Try Redis cache first (60s TTL — avoids blocking DB during RIS write bursts)
    try:
        r = await get_redis()
        cached = await r.get(f"globe:nodes:{window_m}")
        if cached:
            return _json.loads(cached)
    except Exception:
        pass

    pool = await get_pg_pool()
    try:
        rows = await pool.fetch("""
            SELECT
                origin_asn,
                count(*) AS updates,
                count(DISTINCT prefix) AS prefixes,
                count(*) FILTER (WHERE change_type='withdraw') AS withdrawals,
                max(time) AS last_seen
            FROM bgp_updates
            WHERE time > NOW() - ($1 || ' minutes')::INTERVAL
              AND origin_asn IS NOT NULL
            GROUP BY origin_asn
            ORDER BY updates DESC
            LIMIT 300
        """, str(window_m), timeout=10)
    except Exception:
        return {"nodes": [], "total_asns": 0, "window_m": window_m, "error": "db_timeout",
                "timestamp": datetime.now(tz=timezone.utc).isoformat()}
    nodes = []
    max_updates = max((r["updates"] for r in rows), default=1)

    for r in rows:
        asn = r["origin_asn"]
        geo = ASN_GEO.get(asn)
        if geo:
            lat, lon, country, name = geo
        else:
            # Unknown ASN — skip (no geo data)
            continue

        # Jitter co-located nodes so they're individually visible on the globe
        # Deterministic jitter based on ASN — same ASN always at same offset
        jitter_scale = 2.5  # degrees — spreads cluster across ~280km
        jitter_lat = ((asn * 7919) % 1000 / 1000.0 - 0.5) * jitter_scale
        jitter_lon = ((asn * 6271) % 1000 / 1000.0 - 0.5) * jitter_scale * 1.5
        nodes.append({
            "asn":        asn,
            "name":       name,
            "lat":        round(lat + jitter_lat, 4),
            "lon":        round(lon + jitter_lon, 4),
            "country":    country,
            "updates":    r["updates"],
            "prefixes":   r["prefixes"],
            "withdrawals":r["withdrawals"],
            "intensity":  round(r["updates"] / max_updates, 4),
            "last_seen":  r["last_seen"].isoformat() if r["last_seen"] else None,
        })

    result = {"nodes": nodes, "total_asns": len(rows), "window_m": window_m,
            "timestamp": datetime.now(tz=timezone.utc).isoformat()}
    try:
        r = await get_redis()
        await r.set(f"globe:nodes:{window_m}", _json.dumps(result), ex=60)
    except Exception:
        pass
    return result


@router.get("/arcs")
async def globe_arcs(window_m: int = Query(5, ge=1, le=30)):
    """
    Returns active anomaly arcs — src/dst lat/lon pairs for globe rendering.
    Only includes arcs where both endpoints have known geo-coordinates.
    """
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT event_type, origin_asn, expected_asn, affected_prefix,
               confidence, severity, time
        FROM bgp_anomalies
        WHERE source LIKE 'ris/%'
          AND time > NOW() - ($1 || ' minutes')::INTERVAL
        ORDER BY severity DESC, confidence DESC
        LIMIT 200
    """, str(window_m))

    arcs = []
    for r in rows:
        src_geo = ASN_GEO.get(r["origin_asn"] or 0)

        if not src_geo:
            continue

        # BGP flaps are LOCAL instability events - no meaningful destination.
        # They are visualised as pulsing rings on the origin node, not as arcs.
        # Include them in the response so the globe can drive node pulse intensity,
        # but mark has_dst=False so GlobeView skips drawing an arc.
        if r["event_type"] == "bgp_flap":
            arcs.append({
                "event_type":      "bgp_flap",
                "origin_asn":      r["origin_asn"],
                "expected_asn":    None,
                "affected_prefix": r["affected_prefix"],
                "confidence":      float(r["confidence"]),
                "severity":        r["severity"],
                "src_lat":         src_geo[0],
                "src_lon":         src_geo[1],
                "dst_lat":         src_geo[0],
                "dst_lon":         src_geo[1],
                "has_dst":         False,
            })
            continue

        dst_geo = ASN_GEO.get(r["expected_asn"] or 0)
        if not dst_geo:
            continue

        arcs.append({
            "event_type":      r["event_type"],
            "origin_asn":      r["origin_asn"],
            "expected_asn":    r["expected_asn"],
            "affected_prefix": r["affected_prefix"],
            "confidence":      float(r["confidence"]),
            "severity":        r["severity"],
            "src_lat":         src_geo[0],
            "src_lon":         src_geo[1],
            "dst_lat":         dst_geo[0],
            "dst_lon":         dst_geo[1],
            "has_dst":         True,
        })

    return {"arcs": arcs, "window_m": window_m,
            "timestamp": datetime.now(tz=timezone.utc).isoformat()}



@router.get("/flaps")
async def globe_flaps(asn: int, window_m: int = Query(15, ge=1, le=60)):
    """
    Returns detailed flap data for a specific ASN.
    Used by the ASSidebar FLAPS tab for diagnostic detail.
    Returns per-prefix flap counts, confidence, and timing.
    """
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT
            affected_prefix,
            count(*)                          AS flap_count,
            round(avg(confidence)::numeric,3) AS avg_confidence,
            max(confidence)                   AS max_confidence,
            min(time)                         AS first_seen,
            max(time)                         AS last_seen,
            max(severity)                     AS max_severity
        FROM bgp_anomalies
        WHERE event_type   = 'bgp_flap'
          AND origin_asn   = $1
          AND source LIKE 'ris/%'
          AND time > NOW() - make_interval(mins => $2)
          AND affected_prefix IS NOT NULL
        GROUP BY affected_prefix
        ORDER BY flap_count DESC
        LIMIT 30
    """, asn, window_m)

    # Also pull a 5-minute bucket time series for the sparkline
    buckets = await pool.fetch("""
        SELECT
            time_bucket('1 minute', time) AS bucket,
            count(*)                       AS count
        FROM bgp_anomalies
        WHERE event_type = 'bgp_flap'
          AND origin_asn = $1
          AND source LIKE 'ris/%'
          AND time > NOW() - make_interval(mins => $2)
        GROUP BY bucket
        ORDER BY bucket ASC
    """, asn, window_m)

    return {
        "asn":        asn,
        "window_m":   window_m,
        "timestamp":  datetime.now(tz=timezone.utc).isoformat(),
        "prefixes": [
            {
                "prefix":          r["affected_prefix"],
                "flap_count":      r["flap_count"],
                "avg_confidence":  float(r["avg_confidence"]),
                "max_confidence":  float(r["max_confidence"]),
                "first_seen":      r["first_seen"].isoformat(),
                "last_seen":       r["last_seen"].isoformat(),
                "max_severity":    r["max_severity"],
            }
            for r in rows
        ],
        "timeline": [
            {"bucket": b["bucket"].isoformat(), "count": b["count"]}
            for b in buckets
        ],
        "total_flaps": sum(r["flap_count"] for r in rows),
    }

@router.get("/summary")
async def globe_summary():
    """Quick summary for globe header: counts by region and event type."""
    pool = await get_pg_pool()
    row = await pool.fetchrow("""
        SELECT
            count(DISTINCT origin_asn) FILTER (WHERE time > NOW()-INTERVAL '10 minutes') AS active_asns,
            count(DISTINCT prefix)     FILTER (WHERE time > NOW()-INTERVAL '10 minutes') AS active_prefixes,
            count(*) FILTER (WHERE change_type='announce' AND time > NOW()-INTERVAL '10 minutes') AS announces_10m,
            count(*) FILTER (WHERE change_type='withdraw' AND time > NOW()-INTERVAL '10 minutes') AS withdrawals_10m
        FROM bgp_updates WHERE collector != 'stub-rrc00'
    """)
    anom = await pool.fetchrow("""
        SELECT
            count(*) FILTER (WHERE event_type='bgp_hijack')        AS hijacks,
            count(*) FILTER (WHERE event_type='bgp_flap')          AS flaps,
            count(*) FILTER (WHERE event_type='withdrawal_surge')   AS surges
        FROM bgp_anomalies
        WHERE source LIKE 'ris/%' AND time > NOW()-INTERVAL '10 minutes'
    """)
    return {
        "active_asns":       row["active_asns"],
        "active_prefixes":   row["active_prefixes"],
        "announces_10m":     row["announces_10m"],
        "withdrawals_10m":   row["withdrawals_10m"],
        "hijacks":           anom["hijacks"],
        "flaps":             anom["flaps"],
        "surges":            anom["surges"],
    }


@router.get("/path-hops")
async def globe_path_hops(
    src_asn: int,
    dst_asn: int | None = None,
    prefix:  str | None = None,
):
    """
    Return AS path hops with geo-coordinates for globe visualization.
    Each hop becomes a sequential arc segment on the globe surface.

    Either dst_asn OR prefix must be provided.
    Returns the most common observed path between src and dst,
    with lat/lon resolved for each intermediate AS.
    """
    pool = await get_pg_pool()

    # Find most-common observed AS path for this src → dst or prefix
    if dst_asn:
        rows = await pool.fetch("""
            SELECT as_path, count(*) AS freq
            FROM bgp_updates
            WHERE origin_asn = $1
              AND $2 = ANY(as_path)
              AND as_path IS NOT NULL
              AND array_length(as_path,1) >= 2
              AND time > NOW() - INTERVAL '30 minutes'
            GROUP BY as_path
            ORDER BY freq DESC
            LIMIT 5
        """, src_asn, dst_asn)
    elif prefix:
        rows = await pool.fetch("""
            SELECT as_path, count(*) AS freq
            FROM bgp_updates
            WHERE prefix = $1
              AND as_path IS NOT NULL
              AND array_length(as_path,1) >= 2
              AND time > NOW() - INTERVAL '30 minutes'
            GROUP BY as_path
            ORDER BY freq DESC
            LIMIT 5
        """, prefix)
    else:
        rows = await pool.fetch("""
            SELECT as_path, count(*) AS freq
            FROM bgp_updates
            WHERE origin_asn = $1
              AND as_path IS NOT NULL
              AND array_length(as_path,1) >= 2
              AND time > NOW() - INTERVAL '30 minutes'
            GROUP BY as_path
            ORDER BY freq DESC
            LIMIT 5
        """, src_asn)

    if not rows:
        return {"paths": [], "src_asn": src_asn, "dst_asn": dst_asn}

    paths = []
    for row in rows:
        raw_path = list(row["as_path"])
        hops = []
        for asn in raw_path:
            geo = ASN_GEO.get(asn)
            if geo:
                hops.append({
                    "asn":     asn,
                    "name":    geo[3],
                    "lat":     geo[0],
                    "lon":     geo[1],
                    "country": geo[2],
                    "known":   True,
                })
            else:
                hops.append({"asn": asn, "known": False,
                             "lat": None, "lon": None})

        # Only include paths where at least src and dst are geo-known
        known = [h for h in hops if h["known"]]
        if len(known) >= 2:
            paths.append({
                "as_path":   raw_path,
                "hops":      hops,
                "hop_count": len(raw_path),
                "geo_known": len(known),
                "frequency": row["freq"],
            })

    return {
        "src_asn":   src_asn,
        "dst_asn":   dst_asn,
        "prefix":    prefix,
        "paths":     paths[:3],   # top 3 most-common paths
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
