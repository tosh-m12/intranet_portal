"""請求明細の請求先名(bill_to)を名寄せ済みの正規化名へ統一する。

取引先マスタ(MasterParty)は大文字統一・整形済みだったが、個別明細(InvoiceLine.bill_to)は
原本のまま取り込まれ未正規化だった(小文字・㈱・LIMITED・日本漢字 等が混在)。
invoice_ledger の名寄せ結果(std_company, §3ルール)に基づき bill_to を上書きして揃える。
"""
from django.db import migrations

BILL_TO_MAP = {
    '日本通運㈱FBU': '日本通運株式会社FBU',
    '日本通運㈱関東甲信越ブロックFBU': '日本通運株式会社関東甲信越ブロックFBU',
    '日通汽車物流（中国）有限公司': '日通汽车物流（中国）有限公司',
    '日通国際物流（中国）有限公司華東統括支店': '日通国际物流（中国）有限公司华东统括支店',
    '日通国際物流（中国）有限公司蘇州支店': '日通国际物流（中国）有限公司苏州支店',
    '菱集国際貨運代理（上海）有限公司': '菱集国际货运代理（上海）有限公司',
    'VALOR HONG KONG COMPANY LIMITED': 'VALOR HONG KONG COMPANY LTD.',
    'Sharp Hong Kong Limited': 'SHARP HONG KONG LTD.',
    'SEEYU TECHNOLOGY LIMITED': 'SEEYU TECHNOLOGY LTD.',
    '日通国際物流（中国）有限公司杭州分公司': '日通国际物流（中国）有限公司杭州分公司',
    '日通国際物流（中国）有限公司蘇州分公司': '日通国际物流（中国）有限公司苏州分公司',
    '日本通運㈱大阪国際輸送支店': '日本通運株式会社大阪国際輸送支店',
    'KYOEI ELECTRONICS HONG KONG LIMITED': 'KYOEI ELECTRONICS HONG KONG LTD.',
    '日本通運㈱福岡海運支店': '日本通運株式会社福岡海運支店',
    '日通国際物流（中国）有限公司無錫分公司': '日通国际物流（中国）有限公司无锡分公司',
    'Nippon Express (H.K.) Co., Ltd.': 'NIPPON EXPRESS (H.K.) CO., LTD.',
    '日本通運㈱浜松航空支店': '日本通運株式会社浜松航空支店',
    '日本通運㈱グローバルロジスティクス支店': '日本通運株式会社グローバルロジスティクス支店',
    '日本通運㈱国際航空貨物第三営業部': '日本通運株式会社国際航空貨物第三営業部',
    '日本通運㈱京都支店': '日本通運株式会社京都支店',
    'NMB Italia S.R.L.': 'NMB ITALIA S.R.L.',
    '日本通運㈱神戸支店': '日本通運株式会社神戸支店',
    '日本通運㈱成田空港支店': '日本通運株式会社成田空港支店',
    '日本通運㈱水島海運支店': '日本通運株式会社水島海運支店',
    '日通国際物流（中国）有限公司天津分公司': '日通国际物流（中国）有限公司天津分公司',
    '日本通運㈱シャープ大阪事業所': '日本通運株式会社シャープ大阪事業所',
    'HOKURIKU ELECTRIC INDUSTRY CO.,LTD': 'HOKURIKU ELECTRIC INDUSTRY CO., LTD',
    '日本通運㈱航空事業支店': '日本通運株式会社航空事業支店',
    '北陸（上海）国際貿易有限公司': '北陆（上海）国际贸易有限公司',
    '台灣迪恩士先端科技股份有限公司': '台湾迪恩士先端科技股份有限公司',
    '日通国際物流（中国）有限公司青島分公司': '日通国际物流（中国）有限公司青岛分公司',
    '日本通運㈱大阪航空支店': '日本通運株式会社大阪航空支店',
    '日通国际物流（中国）有限公司上海分公司': '日通国际物流（中国）有限公司华东统括支店',
    '日本通運㈱神戸航空支店': '日本通運株式会社神戸航空支店',
    'SHARP HONG KONG LIMITED': 'SHARP HONG KONG LTD.',
    'Seglian Manufacturing Group, Inc.': 'SEGLIAN MANUFACTURING GROUP INC.',
}


def normalize(apps, schema_editor):
    InvoiceLine = apps.get_model('billing', 'InvoiceLine')
    for raw, std in BILL_TO_MAP.items():
        InvoiceLine.objects.filter(bill_to=raw).update(bill_to=std)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('billing', '0002_load_seed')]
    operations = [migrations.RunPython(normalize, noop)]
