/**
 * 케어포 자동 보고 시스템 - Google Apps Script
 *
 * 이 코드는 구글시트 안에서 작동합니다.
 *
 * 사용법:
 * 1. 새 구글시트 생성 → 확장 프로그램 → Apps Script
 * 2. 기본 코드 삭제 후 이 파일 전체 붙여넣기
 * 3. setup() 함수를 한 번 실행 (메뉴: 실행 → setup)
 * 4. 배포 → 새 배포 → 웹앱 → URL 복사
 * 5. 복사한 URL을 Python 앱 설정에 등록
 */

// ====== 지점 설정 (신규 지점 오픈 시 여기에 추가) ======
const BRANCHES = [
  { name: '둔산점', capacity: 76 },
  { name: '서구점', capacity: 84 },
  { name: '천안점', capacity: 82 },
  { name: '청주 오창점', capacity: 62 },
];

const TAB_DATA = '데이터';
const TAB_REPORT = '충청본부 출석인원';
const TAB_MONTHLY = '월간 평균 출석현황';
const TZ = 'Asia/Seoul';


/**
 * 한 번 실행하여 3개 탭과 수식을 생성.
 * 메뉴: 실행 → setup
 */
function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  setupDataTab_(ss);
  setupReportTab_(ss);
  setupMonthlyTab_(ss);

  SpreadsheetApp.getUi().alert('✅ 설정 완료!\n\n다음 단계: 배포 → 새 배포 → 웹앱 → URL 복사');
}


function setupDataTab_(ss) {
  let sheet = ss.getSheetByName(TAB_DATA);
  if (!sheet) sheet = ss.insertSheet(TAB_DATA);
  sheet.clear();

  const headers = [['날짜', '지점', '현원', '결석', '출석', '정원']];
  sheet.getRange(1, 1, 1, 6).setValues(headers)
    .setFontWeight('bold').setBackground('#e8f0fe').setHorizontalAlignment('center');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 110);
  sheet.setColumnWidth(2, 120);
  for (let c = 3; c <= 6; c++) sheet.setColumnWidth(c, 80);
}


function setupReportTab_(ss) {
  let sheet = ss.getSheetByName(TAB_REPORT);
  if (!sheet) sheet = ss.insertSheet(TAB_REPORT);
  sheet.clear();

  // 제목 (오늘 날짜 자동)
  sheet.getRange('A1').setFormula(
    '="지점별 출석 현황 "&TEXT(TODAY(),"yy.MM.dd")&"("&CHOOSE(WEEKDAY(TODAY()),"일","월","화","수","목","금","토")&"요일)"'
  ).setFontSize(14).setFontWeight('bold');
  sheet.getRange(1, 1, 1, BRANCHES.length + 1).merge();

  // 헤더: 항목 + 지점명들
  sheet.getRange(3, 1).setValue('구분').setFontWeight('bold')
    .setBackground('#e8f0fe').setHorizontalAlignment('center');
  for (let j = 0; j < BRANCHES.length; j++) {
    sheet.getRange(3, j + 2).setValue(BRANCHES[j].name)
      .setFontWeight('bold').setBackground('#e8f0fe').setHorizontalAlignment('center');
  }

  // 항목 라벨
  const labels = ['현원(수급중)', '결석', '출석', '정원', '충원율', '정원대비 출석률'];
  for (let i = 0; i < labels.length; i++) {
    sheet.getRange(4 + i, 1).setValue(labels[i]).setFontWeight('bold');
  }

  // 데이터 수식
  for (let j = 0; j < BRANCHES.length; j++) {
    const col = j + 2;
    const br = BRANCHES[j].name;
    const cap = BRANCHES[j].capacity;

    // 현원
    sheet.getRange(4, col).setFormula(
      `=IFERROR(SUMIFS(데이터!C:C, 데이터!A:A, TODAY(), 데이터!B:B, "${br}"), "-")`
    );
    // 결석
    sheet.getRange(5, col).setFormula(
      `=IFERROR(SUMIFS(데이터!D:D, 데이터!A:A, TODAY(), 데이터!B:B, "${br}"), "-")`
    );
    // 출석
    sheet.getRange(6, col).setFormula(
      `=IFERROR(SUMIFS(데이터!E:E, 데이터!A:A, TODAY(), 데이터!B:B, "${br}"), "-")`
    );
    // 정원 (데이터 탭에 있으면 그것 사용, 없으면 BRANCHES 기본값)
    sheet.getRange(7, col).setFormula(
      `=IFERROR(IF(SUMIFS(데이터!F:F, 데이터!A:A, TODAY(), 데이터!B:B, "${br}")=0, ${cap}, SUMIFS(데이터!F:F, 데이터!A:A, TODAY(), 데이터!B:B, "${br}")), ${cap})`
    );
    // 충원율
    const colLetter = String.fromCharCode(65 + col - 1);  // B, C, D, E
    sheet.getRange(8, col).setFormula(
      `=IFERROR(TEXT(${colLetter}4/${colLetter}7, "0.0%"), "-")`
    );
    // 출석률
    sheet.getRange(9, col).setFormula(
      `=IFERROR(TEXT(${colLetter}6/${colLetter}7, "0.0%"), "-")`
    );
  }

  // 표 영역 테두리 + 가운데 정렬
  const tableRange = sheet.getRange(3, 1, 7, BRANCHES.length + 1);
  tableRange.setBorder(true, true, true, true, true, true);
  tableRange.setHorizontalAlignment('center');

  sheet.setColumnWidth(1, 140);
  for (let c = 2; c <= BRANCHES.length + 1; c++) sheet.setColumnWidth(c, 110);
}


function setupMonthlyTab_(ss) {
  let sheet = ss.getSheetByName(TAB_MONTHLY);
  if (!sheet) sheet = ss.insertSheet(TAB_MONTHLY);
  sheet.clear();

  // 제목
  sheet.getRange('A1').setFormula(
    '=YEAR(TODAY())&"년 월별 평균 출석 현황 (영업일 기준)"'
  ).setFontSize(14).setFontWeight('bold');
  sheet.getRange(1, 1, 1, BRANCHES.length + 1).merge();

  // 헤더
  sheet.getRange(3, 1).setValue('월').setFontWeight('bold')
    .setBackground('#e8f0fe').setHorizontalAlignment('center');
  for (let j = 0; j < BRANCHES.length; j++) {
    sheet.getRange(3, j + 2).setValue(BRANCHES[j].name)
      .setFontWeight('bold').setBackground('#e8f0fe').setHorizontalAlignment('center');
  }

  // 1~12월
  for (let m = 1; m <= 12; m++) {
    sheet.getRange(3 + m, 1).setValue(m + '월').setFontWeight('bold');
    for (let j = 0; j < BRANCHES.length; j++) {
      const br = BRANCHES[j].name;
      const col = j + 2;
      sheet.getRange(3 + m, col).setFormula(
        `=IFERROR(ROUND(AVERAGEIFS(데이터!E:E, 데이터!A:A, ">="&DATE(YEAR(TODAY()),${m},1), 데이터!A:A, "<"&DATE(YEAR(TODAY()),${m+1},1), 데이터!B:B, "${br}"), 1), "-")`
      );
    }
  }

  // 테두리 + 가운데 정렬
  const tableRange = sheet.getRange(3, 1, 13, BRANCHES.length + 1);
  tableRange.setBorder(true, true, true, true, true, true);
  tableRange.setHorizontalAlignment('center');

  sheet.setColumnWidth(1, 80);
  for (let c = 2; c <= BRANCHES.length + 1; c++) sheet.setColumnWidth(c, 110);
}


/**
 * Python 앱이 매일 호출하는 webhook 엔드포인트.
 *
 * Payload 형식:
 *   {
 *     "date": "2026-06-19",
 *     "branches": [
 *       {"name":"둔산점", "hyeon_won":73, "gyeol_seok":0, "chul_seok":63, "capacity":76},
 *       ...
 *     ]
 *   }
 */
function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);
    if (!payload.date || !Array.isArray(payload.branches)) {
      throw new Error('payload format error: need {date, branches}');
    }

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(TAB_DATA);
    if (!sheet) throw new Error(`'${TAB_DATA}' 탭이 없음. setup() 먼저 실행하세요.`);

    const lastRow = sheet.getLastRow();
    const existing = lastRow > 1
      ? sheet.getRange(2, 1, lastRow - 1, 2).getValues()
      : [];

    // key(날짜+지점) → 행번호 매핑
    const idx = {};
    existing.forEach((row, i) => {
      const d = row[0] instanceof Date
        ? Utilities.formatDate(row[0], TZ, 'yyyy-MM-dd')
        : String(row[0]).trim();
      const key = d + '|' + String(row[1]).trim();
      idx[key] = i + 2;  // 헤더(1) + 인덱스(0-based) → 1-based 행번호
    });

    const newRows = [];
    let updated = 0;
    payload.branches.forEach(b => {
      const row = [payload.date, b.name, b.hyeon_won, b.gyeol_seok, b.chul_seok, b.capacity];
      const key = payload.date + '|' + b.name;
      if (idx[key]) {
        sheet.getRange(idx[key], 1, 1, 6).setValues([row]);
        updated++;
      } else {
        newRows.push(row);
      }
    });

    if (newRows.length > 0) {
      sheet.getRange(sheet.getLastRow() + 1, 1, newRows.length, 6).setValues(newRows);
    }

    return ContentService
      .createTextOutput(JSON.stringify({
        ok: true,
        inserted: newRows.length,
        updated: updated,
        date: payload.date,
      }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}


/**
 * Webhook 동작 테스트용. Apps Script 편집기에서 직접 실행해 확인.
 */
function testDoPost() {
  const sample = {
    postData: {
      contents: JSON.stringify({
        date: '2026-06-19',
        branches: [
          { name: '둔산점', hyeon_won: 73, gyeol_seok: 0, chul_seok: 63, capacity: 76 },
        ],
      }),
    },
  };
  const result = doPost(sample);
  Logger.log(result.getContent());
}
