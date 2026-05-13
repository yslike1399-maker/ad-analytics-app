#!/usr/bin/env python
"""네이버 검색광고 API 동기화 스크립트

CUSTOMER_ID, API_KEY, SECRET_KEY를 ../naver_credentials.json에서 읽어
캠페인 / 광고그룹 / 키워드(입찰가·품질지수 포함) / 기간 합산 성과를
../samples/ 폴더에 JSON+CSV로 저장.

사용:
    python tools/fetch_naver_ads.py             # 최근 12일
    python tools/fetch_naver_ads.py --days 7    # 최근 7일
    python tools/fetch_naver_ads.py --skip-stats  # 통계 건너뛰기 (구조만)

출력:
    samples/naver_api_campaigns.json      — 캠페인 목록 (예산, 상태)
    samples/naver_api_adgroups.json       — 광고그룹 (입찰가, 타겟팅)
    samples/naver_api_keywords.json       — 키워드 (입찰가, 품질지수)
    samples/naver_api_stats.json          — 키워드별 기간 합산 통계
    samples/naver_api_export_YYYYMMDD.csv — 광고 분석 탭 호환 CSV
"""

import hashlib
import hmac
import base64
import time
import json
import csv
import os
import sys
import argparse
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = 'https://api.searchad.naver.com'


# ───────── 자격증명 ─────────
def load_credentials():
    here = os.path.dirname(os.path.abspath(__file__))
    cred_path = os.path.join(here, '..', 'naver_credentials.json')
    if not os.path.exists(cred_path):
        print(f"❌ 자격증명 파일 없음: {cred_path}")
        print("   naver_credentials.template.json 을 'naver_credentials.json'으로 복사 후 키 입력.")
        sys.exit(1)
    with open(cred_path, encoding='utf-8') as f:
        c = json.load(f)
    for k in ('CUSTOMER_ID', 'API_KEY', 'SECRET_KEY'):
        if not c.get(k) or '여기에' in str(c[k]):
            print(f"❌ {k}이(가) 비어있거나 템플릿 그대로. 실제 값을 입력해주세요.")
            sys.exit(1)
    return c


# ───────── HMAC 서명 ─────────
def _sign(method, uri, timestamp, secret_key):
    msg = f"{timestamp}.{method}.{uri}"
    sig = hmac.new(secret_key.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).digest()
    return base64.b64encode(sig).decode('utf-8')


def _request(method, uri, creds, body=None):
    """기본 인증 요청 (X-Timestamp / X-API-KEY / X-Customer / X-Signature)"""
    timestamp = str(int(time.time() * 1000))
    # 서명에는 query string 제외한 path만 사용
    path_for_sig = uri.split('?', 1)[0]
    signature = _sign(method, path_for_sig, timestamp, creds['SECRET_KEY'])

    url = BASE_URL + uri
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('X-Timestamp', timestamp)
    req.add_header('X-API-KEY', creds['API_KEY'])
    req.add_header('X-Customer', str(creds['CUSTOMER_ID']))
    req.add_header('X-Signature', signature)
    req.add_header('Content-Type', 'application/json; charset=UTF-8')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        print(f"❌ HTTP {e.code} on {method} {uri}: {err_body[:300]}")
        raise


def api_get(uri, creds):
    return _request('GET', uri, creds)


def api_post(uri, creds, body):
    return _request('POST', uri, creds, body=body)


# ───────── 데이터 수집 ─────────
def fetch_campaigns(creds):
    print("📥 캠페인 조회...")
    return api_get('/ncc/campaigns', creds) or []


def fetch_adgroups(creds, campaign_ids):
    print(f"📥 광고그룹 조회 ({len(campaign_ids)}개 캠페인)...")
    adgroups = []
    for cid in campaign_ids:
        try:
            gs = api_get(f'/ncc/adgroups?nccCampaignId={cid}', creds) or []
            adgroups.extend(gs)
        except Exception as e:
            print(f"   ⚠ 캠페인 {cid} 광고그룹 조회 실패: {e}")
    return adgroups


def fetch_keywords(creds, adgroup_ids):
    print(f"📥 키워드 조회 ({len(adgroup_ids)}개 광고그룹)...")
    keywords = []
    for gid in adgroup_ids:
        try:
            ks = api_get(f'/ncc/keywords?nccAdgroupId={gid}', creds) or []
            keywords.extend(ks)
        except Exception as e:
            print(f"   ⚠ 광고그룹 {gid} 키워드 조회 실패: {e}")
    return keywords


def create_stat_report(creds, report_tp, stat_dt):
    """비동기 보고서 생성 요청.
    report_tp: 'AD', 'AD_KEYWORD', 'KEYWORD', 'EXPANSION', 'AD_DETAIL_PROMOTION', ...
    stat_dt:   'YYYY-MM-DD' (단일 날짜, 일별 보고서)
    """
    body = {'reportTp': report_tp, 'statDt': stat_dt}
    return api_post('/stat-reports', creds, body)


def poll_stat_report(creds, job_id, max_wait_sec=180, interval=5):
    """보고서 완료 폴링. status가 'BUILT' 또는 'DONE'이면 반환."""
    start = time.time()
    while time.time() - start < max_wait_sec:
        resp = api_get(f'/stat-reports/{job_id}', creds)
        status = (resp or {}).get('status') or ''
        if status in ('BUILT', 'DONE', 'SUCCESS'):
            return resp
        if status in ('FAIL', 'FAILED', 'ERROR', 'NONE'):
            return None
        time.sleep(interval)
    return None


def download_text(url, creds, timeout=120):
    """API 다운로드 URL에서 텍스트 다운로드 (인증 헤더 필요)"""
    from urllib.parse import urlparse
    pu = urlparse(url)
    path_with_query = pu.path + (('?' + pu.query) if pu.query else '')
    sig_path = path_with_query.split('?', 1)[0]
    timestamp = str(int(time.time() * 1000))
    signature = _sign('GET', sig_path, timestamp, creds['SECRET_KEY'])

    req = urllib.request.Request(url, method='GET')
    req.add_header('X-Timestamp', timestamp)
    req.add_header('X-API-KEY', creds['API_KEY'])
    req.add_header('X-Customer', str(creds['CUSTOMER_ID']))
    req.add_header('X-Signature', signature)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if raw[:2] == b'\x1f\x8b':
        import gzip
        raw = gzip.decompress(raw)
    return raw.decode('utf-8', errors='ignore')


def parse_tsv(text):
    """탭 또는 콤마 구분 보고서 파싱. 첫 줄을 헤더로 사용. 헤더가 없는 경우도 처리."""
    lines = [ln for ln in text.split('\n') if ln.strip()]
    if not lines: return [], []
    # 구분자 자동 감지
    delim = '\t' if '\t' in lines[0] else ','
    rows = [ln.split(delim) for ln in lines]
    # 헤더 추정: 첫 줄에 한글/영문 라벨이 있으면 헤더
    first = rows[0]
    has_header = any(not (c.replace('.', '').replace('-', '').isdigit() or c == '') for c in first if c)
    if has_header:
        headers = first
        data = [dict(zip(headers, r)) for r in rows[1:]]
    else:
        headers = [f'col{i}' for i in range(len(first))]
        data = [dict(zip(headers, r)) for r in rows]
    return headers, data


def fetch_ad_reports(creds, days=12, out_dir=None):
    """AD 보고서 (광고소재 × 디바이스 × 일 단위) 비동기 조회.

    매일 1개 보고서 생성 → 폴링 → 다운로드. statDt는 단일 날짜라 N일 = N번 호출.
    raw TSV는 디스크에 저장 (컬럼 의미 검증 후 사용).

    AD 보고서 컬럼 (positional, 14개):
      1) statDt        2) customerId    3) campaignId      4) adgroupId
      5) keywordId(-)  6) adId          7) bizChannelId    8) media
      9) device(P/M)   10) impCnt       11) clkCnt         12) cost
      13) ?            14) conv?

    ※ 13,14열 의미 미확인 — raw TSV 저장 후 검증 권장.
    """
    print(f"\n📋 AD 보고서 비동기 조회 (광고소재 × 디바이스 × 일, 최근 {days}일)...")
    end_date = datetime.now().date() - timedelta(days=1)
    all_rows = []
    for offset in range(days):
        date_str = (end_date - timedelta(days=offset)).strftime('%Y-%m-%d')
        try:
            job = create_stat_report(creds, 'AD', date_str)
            job_id = (job or {}).get('reportJobId')
            if not job_id:
                print(f"   [{date_str}] ⚠ jobId 없음")
                continue
            ready = poll_stat_report(creds, job_id, max_wait_sec=120)
            if not ready:
                print(f"   [{date_str}] ⚠ 시간 초과/실패")
                continue
            url = ready.get('downloadUrl')
            if not url:
                print(f"   [{date_str}] ⚠ downloadUrl 없음")
                continue
            text = download_text(url, creds)
            lines = [ln for ln in text.split('\n') if ln.strip()]

            # raw TSV 디스크 저장 (검증용)
            if out_dir:
                raw_path = os.path.join(out_dir, f'naver_api_ad_report_{date_str}.tsv')
                with open(raw_path, 'w', encoding='utf-8') as f:
                    f.write(text)

            # 컬럼 positional 파싱
            day_rows = []
            for ln in lines:
                cells = ln.split('\t')
                if len(cells) < 12:
                    continue
                day_rows.append({
                    'statDt':       cells[0],
                    'customerId':   cells[1],
                    'campaignId':   cells[2],
                    'adgroupId':    cells[3],
                    'keywordId':    cells[4] if cells[4] != '-' else None,
                    'adId':         cells[5],
                    'bizChannelId': cells[6] if len(cells) > 6 else None,
                    'media':        cells[7] if len(cells) > 7 else None,
                    'device':       cells[8] if len(cells) > 8 else None,
                    'impCnt':       int(cells[9]) if len(cells) > 9 and cells[9].isdigit() else 0,
                    'clkCnt':       int(cells[10]) if len(cells) > 10 and cells[10].isdigit() else 0,
                    'cost':         float(cells[11]) if len(cells) > 11 and cells[11].replace('.','').isdigit() else 0.0,
                    'col13_raw':    cells[12] if len(cells) > 12 else None,
                    'col14_raw':    cells[13] if len(cells) > 13 else None,
                })
            all_rows.extend(day_rows)
            print(f"   [{date_str}] → {len(day_rows)}행 (raw 저장)")
            time.sleep(0.8)
        except urllib.error.HTTPError as e:
            print(f"   [{date_str}] ⚠ HTTP {e.code}")
        except Exception as e:
            print(f"   [{date_str}] ⚠ {type(e).__name__}: {str(e)[:120]}")
    return all_rows


def aggregate_ad_report_by_device(ad_rows, adgroup_filter=None):
    """(adgroupId, device, statDt) 단위로 합계. SHOPPING/CATALOG 광고그룹만 필터.
    statDt는 YYYYMMDD 형식 → YYYY-MM-DD로 변환."""
    agg = {}
    for r in ad_rows:
        gid = r.get('adgroupId')
        if adgroup_filter and gid not in adgroup_filter:
            continue
        dev = r.get('device') or '?'
        date_raw = r.get('statDt') or ''
        # YYYYMMDD → YYYY-MM-DD
        date_fmt = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}" if len(date_raw) == 8 and date_raw.isdigit() else date_raw
        key = (gid, dev, date_fmt)
        if key not in agg:
            agg[key] = {'adgroupId': gid, 'device': dev, 'statDt': date_fmt,
                        'impCnt': 0, 'clkCnt': 0, 'cost': 0.0}
        agg[key]['impCnt'] += r.get('impCnt', 0)
        agg[key]['clkCnt'] += r.get('clkCnt', 0)
        agg[key]['cost']   += r.get('cost', 0.0)
    return list(agg.values())


def fetch_stats(creds, ids, days=12):
    """키워드 ID 배열 → 기간 합산 통계 (배치 처리)"""
    if not ids:
        return []
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    time_range = json.dumps({
        'since': start_date.strftime('%Y-%m-%d'),
        'until': end_date.strftime('%Y-%m-%d'),
    })
    fields = json.dumps(['impCnt', 'clkCnt', 'salesAmt', 'ccnt', 'convAmt'])

    BATCH = 100  # 안전한 배치 사이즈
    all_stats = []
    for i in range(0, len(ids), BATCH):
        batch = ids[i:i + BATCH]
        ids_str = ','.join(batch)
        uri = (f'/stats?ids={urllib.parse.quote(ids_str)}'
               f'&fields={urllib.parse.quote(fields)}'
               f'&timeRange={urllib.parse.quote(time_range)}')
        try:
            resp = api_get(uri, creds)
            data = (resp or {}).get('data', []) if isinstance(resp, dict) else (resp or [])
            all_stats.extend(data)
            print(f"   ... 배치 {i // BATCH + 1}/{(len(ids) + BATCH - 1) // BATCH} ({len(data)}건)")
            time.sleep(0.2)  # rate limit 보호
        except Exception as e:
            print(f"   ⚠ 배치 {i // BATCH + 1} 통계 실패: {e}")
    return all_stats


# ───────── 출력 ─────────
def save_json(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def save_csv(rows, columns, path):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, '') for c in columns})


def export_csv_for_app(campaigns, adgroups, keywords, stats, grp_stats, path, ad_device_agg=None,
                       period_start=None, period_end=None):
    """우리 앱의 광고 분석 탭이 인식하는 네이버 CSV 컬럼 형식으로 저장.

    stats:          키워드 단위 통계 (WEB_SITE / SHOPPING_BRAND 키워드 광고용)
    grp_stats:      광고그룹 단위 통계 (SHOPPING / CATALOG 자동 매칭 광고용)
    ad_device_agg:  AD 보고서 디바이스 집계 (SHOPPING/CATALOG의 PC/모바일 split, 실제 일별 데이터)
    period_start:   분석 기간 시작일 (합계 데이터의 일별 표시용)
    period_end:     분석 기간 종료일 (앞뒤 anchor 용)
    """
    camp_by_id = {c.get('nccCampaignId'): c for c in campaigns}
    grp_by_id  = {g.get('nccAdgroupId'): g for g in adgroups}
    kw_by_id   = {k.get('nccKeywordId'): k for k in keywords}

    rows = []
    # 합계 데이터에 쓸 날짜: 기간 시작일 우선, 없으면 오늘
    agg_date = period_start or datetime.now().strftime('%Y-%m-%d')

    # 1) 키워드 단위 행 (파워링크 / 쇼핑브랜드 등) — 기간 합계라서 시작일로 표시
    for s in stats:
        kw_id = s.get('id') or s.get('nccKeywordId')
        kw   = kw_by_id.get(kw_id, {})
        grp  = grp_by_id.get(kw.get('nccAdgroupId'), {})
        camp = camp_by_id.get(grp.get('nccCampaignId'), {})

        rows.append({
            '일별':            s.get('statDt') or agg_date,
            '캠페인':           camp.get('name', ''),
            '캠페인유형':       camp.get('campaignTp', ''),
            '광고그룹':         grp.get('name', ''),
            '광고그룹유형':     grp.get('adgroupType', ''),
            '키워드':           kw.get('keyword', ''),
            '노출수':           s.get('impCnt', 0),
            '클릭수':           s.get('clkCnt', 0),
            '총비용(VAT포함)':  s.get('salesAmt', 0),
            '전환수':           s.get('ccnt', 0),
            '전환매출액':       s.get('convAmt', 0),
        })

    # 2) 광고그룹 단위 행 (SHOPPING / CATALOG)
    # ad_device_agg가 있으면 디바이스 split 행으로 출력 (PC/모바일 분리)
    # 매출/전환은 grp_stats 합계를 클릭 비율로 디바이스에 비례 배분
    dev_by_grp = {}  # grp_id → { 'P': {agg, statDt_first}, 'M': ... }
    if ad_device_agg:
        for r in ad_device_agg:
            gid = r['adgroupId']
            dev = r['device']
            if gid not in dev_by_grp:
                dev_by_grp[gid] = {}
            if dev not in dev_by_grp[gid]:
                dev_by_grp[gid][dev] = {'impCnt': 0, 'clkCnt': 0, 'cost': 0.0, 'statDt_first': r.get('statDt')}
            dev_by_grp[gid][dev]['impCnt'] += r['impCnt']
            dev_by_grp[gid][dev]['clkCnt'] += r['clkCnt']
            dev_by_grp[gid][dev]['cost']   += r['cost']

    DEVICE_LABEL = {'P': 'PC', 'M': '모바일', 'Z': '기타'}

    for s in (grp_stats or []):
        grp_id = s.get('id') or s.get('nccAdgroupId')
        grp = grp_by_id.get(grp_id, {})
        camp = camp_by_id.get(grp.get('nccCampaignId'), {})
        grp_name = grp.get('name', '')

        devs = dev_by_grp.get(grp_id)
        if devs:
            # 디바이스 split 행 출력 (실제 일별 데이터)
            total_clk = sum(d['clkCnt'] for d in devs.values()) or 1
            for dev_key, agg in devs.items():
                ratio = (agg['clkCnt'] / total_clk) if total_clk > 0 else 0
                rows.append({
                    '일별':            agg.get('statDt_first') or agg_date,
                    '캠페인':           camp.get('name', ''),
                    '캠페인유형':       camp.get('campaignTp', ''),
                    '광고그룹':         f"{grp_name} [{DEVICE_LABEL.get(dev_key, dev_key)}]",
                    '광고그룹유형':     grp.get('adgroupType', ''),
                    '키워드':           '',
                    '노출수':           agg['impCnt'],
                    '클릭수':           agg['clkCnt'],
                    '총비용(VAT포함)':  round(agg['cost'], 0),
                    '전환수':           round((s.get('ccnt')   or 0) * ratio, 1),
                    '전환매출액':       round((s.get('convAmt') or 0) * ratio, 0),
                })
        else:
            # 디바이스 데이터 없으면 기존대로 합계 행 (기간 시작일로 표시)
            rows.append({
                '일별':            s.get('statDt') or agg_date,
                '캠페인':           camp.get('name', ''),
                '캠페인유형':       camp.get('campaignTp', ''),
                '광고그룹':         grp_name,
                '광고그룹유형':     grp.get('adgroupType', ''),
                '키워드':           '',
                '노출수':           s.get('impCnt', 0),
                '클릭수':           s.get('clkCnt', 0),
                '총비용(VAT포함)':  s.get('salesAmt', 0),
                '전환수':           s.get('ccnt', 0),
                '전환매출액':       s.get('convAmt', 0),
            })

    # 3) AD 보고서가 있으면 키워드별 일별 분포 행 추가 (period 종료일까지 range 보장)
    # AD 보고서 device split은 이미 일별 데이터 포함, 키워드 합계는 시작일만 → range 끊김
    # 안전망: 기간 종료일에 빈 anchor 행 추가 (모든 metric=0)
    if period_end and not ad_device_agg:
        rows.append({
            '일별':            period_end,
            '캠페인':           '',
            '캠페인유형':       '',
            '광고그룹':         '',
            '광고그룹유형':     '',
            '키워드':           '',
            '노출수':           0,
            '클릭수':           0,
            '총비용(VAT포함)':  0,
            '전환수':           0,
            '전환매출액':       0,
        })

    columns = ['일별', '캠페인', '캠페인유형', '광고그룹', '광고그룹유형', '키워드',
               '노출수', '클릭수', '총비용(VAT포함)', '전환수', '전환매출액']
    save_csv(rows, columns, path)
    return len(rows)


# ───────── 메인 ─────────
def main():
    parser = argparse.ArgumentParser(description='네이버 검색광고 API 동기화')
    parser.add_argument('--days', type=int, default=12, help='조회 일수 (기본 12일)')
    parser.add_argument('--skip-stats', action='store_true', help='통계 조회 건너뛰기 (구조만)')
    parser.add_argument('--ad-report', action='store_true',
                        help='AD 보고서 (광고소재 × 디바이스) 비동기 조회 — 쇼핑검색 PC/모바일 분리')
    parser.add_argument('--ad-report-days', type=int, default=None,
                        help='AD 보고서 조회 일수 (기본 --days와 동일)')
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.abspath(os.path.join(here, '..', 'samples'))
    os.makedirs(out_dir, exist_ok=True)

    creds = load_credentials()
    print(f"네이버 SA API 동기화 시작\n  CUSTOMER_ID: {creds['CUSTOMER_ID']}\n  기간: 최근 {args.days}일\n")

    # 1. 캠페인
    campaigns = fetch_campaigns(creds)
    print(f"  → {len(campaigns)}개 캠페인")
    save_json(campaigns, os.path.join(out_dir, 'naver_api_campaigns.json'))

    # 2. 광고그룹
    campaign_ids = [c['nccCampaignId'] for c in campaigns if c.get('nccCampaignId')]
    adgroups = fetch_adgroups(creds, campaign_ids)
    print(f"  → {len(adgroups)}개 광고그룹")
    save_json(adgroups, os.path.join(out_dir, 'naver_api_adgroups.json'))

    # 3. 키워드
    adgroup_ids = [g['nccAdgroupId'] for g in adgroups if g.get('nccAdgroupId')]
    keywords = fetch_keywords(creds, adgroup_ids)
    print(f"  → {len(keywords)}개 키워드")
    save_json(keywords, os.path.join(out_dir, 'naver_api_keywords.json'))

    print(f"\n✓ 구조 데이터 저장 완료 → {out_dir}/")

    # 4. 통계
    if not args.skip_stats:
        # 4-a. 키워드 단위 (WEB_SITE / SHOPPING_BRAND)
        stats = []
        if keywords:
            print(f"\n📊 키워드 단위 통계 조회 (최근 {args.days}일)...")
            keyword_ids = [k['nccKeywordId'] for k in keywords]
            stats = fetch_stats(creds, keyword_ids, days=args.days)
            save_json(stats, os.path.join(out_dir, 'naver_api_stats.json'))
            print(f"  → 키워드 통계 {len(stats)}건 저장")

        # 4-b. 광고그룹 단위 (SHOPPING / CATALOG — 자동 매칭 키워드 운영)
        grp_stats = []
        shopping_grp_ids = [g['nccAdgroupId'] for g in adgroups
                            if g.get('adgroupType') in ('SHOPPING', 'CATALOG')]
        if shopping_grp_ids:
            print(f"\n📊 쇼핑검색·카탈로그 광고그룹 단위 통계 조회 ({len(shopping_grp_ids)}개)...")
            grp_stats = fetch_stats(creds, shopping_grp_ids, days=args.days)
            save_json(grp_stats, os.path.join(out_dir, 'naver_api_grp_stats.json'))
            print(f"  → 광고그룹 통계 {len(grp_stats)}건 저장")

        # 4-c. AD 보고서 (옵션) — 광고소재 × 디바이스 × 일 단위 (쇼핑검색 PC/모바일 분리용)
        ad_rows = []
        if args.ad_report:
            ad_days = args.ad_report_days or args.days
            ad_rows = fetch_ad_reports(creds, days=ad_days, out_dir=out_dir)
            if ad_rows:
                save_json(ad_rows, os.path.join(out_dir, 'naver_api_ad_report.json'))
                print(f"  → AD 보고서 원본 {len(ad_rows)}행 저장")

                # 쇼핑검색·카탈로그 광고그룹의 디바이스 단위 합계
                shopping_ids = set(g['nccAdgroupId'] for g in adgroups
                                   if g.get('adgroupType') in ('SHOPPING', 'CATALOG'))
                dev_agg = aggregate_ad_report_by_device(ad_rows, adgroup_filter=shopping_ids)
                save_json(dev_agg, os.path.join(out_dir, 'naver_api_ad_device_agg.json'))
                print(f"  → 쇼핑검색 광고그룹 × 디바이스 집계 {len(dev_agg)}행 저장")

                # AD 보고서 컬럼 의미 검증을 위한 샘플 출력
                non_zero = [r for r in ad_rows if r.get('clkCnt', 0) > 0][:5]
                if non_zero:
                    print(f"\n  📋 컬럼 검증 샘플 (클릭 발생 행 5개):")
                    print(f"     col13(미확인) / col14(미확인) — 임프/클릭/비용과 같이 의미 파악 필요")
                    for r in non_zero:
                        print(f"     · imp={r['impCnt']} clk={r['clkCnt']} cost={r['cost']} col13={r['col13_raw']} col14={r['col14_raw']}")

        # 우리 앱 광고 분석 탭 호환 CSV (키워드 + 광고그룹 단위 + 디바이스 split 모두 포함)
        # ad_device_agg가 있으면 쇼핑/카탈로그는 PC/모바일로 분할 출력 (매출은 클릭 비례 배분)
        dev_agg_for_csv = None
        if args.ad_report and ad_rows:
            shopping_ids = set(g['nccAdgroupId'] for g in adgroups
                               if g.get('adgroupType') in ('SHOPPING', 'CATALOG'))
            dev_agg_for_csv = aggregate_ad_report_by_device(ad_rows, adgroup_filter=shopping_ids)

        # 분석 기간 (합계 데이터에 사용)
        end_date_dt = datetime.now().date() - timedelta(days=1)  # 어제까지 (오늘은 데이터 가공 중)
        start_date_dt = end_date_dt - timedelta(days=args.days - 1)
        period_start = start_date_dt.strftime('%Y-%m-%d')
        period_end   = end_date_dt.strftime('%Y-%m-%d')

        csv_path = os.path.join(out_dir, f'naver_api_export_{datetime.now():%Y%m%d}.csv')
        count = export_csv_for_app(campaigns, adgroups, keywords, stats, grp_stats, csv_path,
                                   ad_device_agg=dev_agg_for_csv,
                                   period_start=period_start, period_end=period_end)
        print(f"\n  → 앱 호환 CSV {count}행 저장: {os.path.basename(csv_path)}")
        print(f"     분석 기간: {period_start} ~ {period_end}")

    print("\n✅ 동기화 완료. 우리 앱에서 samples/ 파일들 활용 가능.")


def build_query_csv_rows(rows, headers, grp_by_id, camp_by_id):
    """검색어 보고서 행 → 우리 앱 CSV 형식으로 변환.

    헤더는 보고서 타입에 따라 다르므로 다양한 컬럼명 후보를 시도.
    """
    if not rows: return []

    # 컬럼명 후보 (한글/영문/약어 모두 시도)
    def find_col(candidates, header_set):
        for c in candidates:
            for h in header_set:
                if c == h or c in h:
                    return h
        return None

    hset = set(headers or [])
    col_grp     = find_col(['광고그룹ID', '광고그룹 ID', 'adGroupId', 'ADGROUP_ID', 'AdGroup ID', '광고그룹'], hset)
    col_query   = find_col(['검색어', '키워드', 'searchQuery', 'keyword', '검색 키워드'], hset)
    col_imp     = find_col(['노출수', '노출', 'impressions', 'impCnt'], hset)
    col_clk     = find_col(['클릭수', '클릭', 'clicks', 'clkCnt'], hset)
    col_cost    = find_col(['총비용(VAT포함)', '총비용', '비용', 'cost', 'salesAmt'], hset)
    col_conv    = find_col(['전환수', 'conversions', 'ccnt'], hset)
    col_rev     = find_col(['전환매출액', '매출', 'revenue', 'convAmt'], hset)
    col_date    = find_col(['통계일', '일별', '날짜', 'statDt', '_statDt'], hset)

    out = []
    for r in rows:
        grp_id  = r.get(col_grp) if col_grp else None
        query   = r.get(col_query, '') if col_query else ''
        grp     = grp_by_id.get(grp_id, {}) if grp_id else {}
        camp    = camp_by_id.get(grp.get('nccCampaignId'), {}) if grp else {}
        out.append({
            '일별':            r.get(col_date) or r.get('_statDt', ''),
            '캠페인':           camp.get('name', ''),
            '캠페인유형':       camp.get('campaignTp', ''),
            '광고그룹':         grp.get('name', ''),
            '광고그룹유형':     grp.get('adgroupType', ''),
            '키워드':           query,
            '노출수':           r.get(col_imp, 0) if col_imp else 0,
            '클릭수':           r.get(col_clk, 0) if col_clk else 0,
            '총비용(VAT포함)':  r.get(col_cost, 0) if col_cost else 0,
            '전환수':           r.get(col_conv, 0) if col_conv else 0,
            '전환매출액':       r.get(col_rev, 0) if col_rev else 0,
        })
    return out


if __name__ == '__main__':
    main()
