/*
 * selfcheck.js — автотест логики «доски заказов».
 *
 * Зачем: доска сама считает просрочки и маржу, и этим цифрам будут верить.
 * Скрипт вытаскивает расчётное ядро прямо из index.html (всё, что идёт до
 * блока отрисовки) и проверяет его на демо-данных и на «чужом» CSV.
 *
 * Запуск:  node selfcheck.js     (нужен Node.js, ничего ставить не надо)
 */
'use strict';
var fs = require('fs');
var path = require('path');

var html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
var m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { throw new Error('в index.html не найден <script>'); }

var MARK = '/* ---------- формат вывода ---------- */';
var core = m[1].slice(0, m[1].indexOf(MARK));
if (!core) { throw new Error('не найдено расчётное ядро (маркер ' + MARK + ')'); }

/* чужой файл: разделитель — запятая, даты dd.mm.yyyy, кавычки, десятичная запятая */
var foreign = [
  'номер,клиент,менеджер,стадия,сумма,себестоимость,дата стадии,план сдачи,претензия,примечание',
  'A-1,"ООО Ромашка, отдел",Иванов,Монтаж,"1 250 000,5",900000,01.10.2026,20.10.2026,yes,ok',
  'A-2,Петров,Сидоров,Unknownstage,300000,200000,2026-10-05,05.11.2026,нет,'
].join('\n');

var tests = [
  "var fails = 0;",
  "function ok(name, cond, info) {",
  "  if (cond) { console.log('  ok   ' + name); }",
  "  else { fails++; console.log('  FAIL ' + name + (info === undefined ? '' : ' -> ' + info)); }",
  "}",
  "function byId(rows, id) { return rows.filter(function (r) { return r.raw.id === id; })[0]; }",
  "var orders = parseCSV(DEMO_CSV);",
  "state.orders = orders;",
  "var rows = orders.map(enrich);",
  "var t = totals(rows);",
  "var f = parseCSV(FOREIGN);",
  "state.orders = orders.concat(f);",
  "ok('демо-файл разобран целиком (15 заказов)', orders.length === 15, orders.length);",
  "ok('номер заказа прочитан из столбца «№»', orders[0].id === 'ЗК-1041' && orders[14].id === 'ЗК-1055');",
  "ok('просрочено = 8', t.hot === 8, t.hot);",
  "ok('стадий сверх норматива = 7', t.breach === 7, t.breach);",
  "ok('сорван план сдачи = 3', t.plan === 3, t.plan);",
  "ok('рекламаций = 3', t.complaints === 3, t.complaints);",
  "ok('сумма портфеля = 9 276 000', t.amount === 9276000, t.amount);",
  "ok('маржа портфеля = 2 116 000', t.margin === 2116000, t.margin);",
  "ok('средняя маржа ≈ 22,8 %', Math.abs(t.marginPct - 22.8) < 0.1, t.marginPct.toFixed(2));",
  "ok('доля просрочки ≈ 53,3 %', Math.abs(t.hotPct - 53.3) < 0.1, t.hotPct.toFixed(2));",
  "ok('распределение: Производство = 3', t.byStage['Производство'].n === 3, t.byStage['Производство'].n);",
  "ok('в модели все 6 стадий', Object.keys(t.byStage).length === 6, Object.keys(t.byStage).join('/'));",
  "ok('ЗК-1048: 39 дней в стадии = просрочка', byId(rows, 'ЗК-1048').stageBreach === true && byId(rows, 'ЗК-1048').inStage === 39);",
  "ok('ЗК-1048: план сорван на 5 дней', byId(rows, 'ЗК-1048').planLateDays === 5, byId(rows, 'ЗК-1048').planLateDays);",
  "ok('ЗК-1048: маржа 32,1 %', Math.abs(byId(rows, 'ЗК-1048').marginPct - 32.1) < 0.1);",
  "ok('ЗК-1047: в графике, не подсвечен', byId(rows, 'ЗК-1047').overdue === false);",
  "ok('ЗК-1046: сорван план и есть рекламация', byId(rows, 'ЗК-1046').planLate === true && byId(rows, 'ЗК-1046').raw.complaint === true);",
  "ok('ЗК-1050: 18 дней при норме 21 — ещё не просрочен', byId(rows, 'ЗК-1050').overdue === false && byId(rows, 'ЗК-1050').inStage === 18);",
  "var back = parseCSV(serializeCSV(orders));",
  "ok('экспорт → импорт: число строк', back.length === orders.length, back.length);",
  "ok('экспорт → импорт: суммы', back[0].amount === 486000 && back[14].cost === 728000);",
  "ok('экспорт → импорт: номера заказов', back[5].id === 'ЗК-1046', back[5].id);",
  "ok('экспорт → импорт: даты', back[0].planDate === '2026-10-20' && back[5].stageSince === '2026-09-29');",
  "ok('экспорт → импорт: рекламация', back[5].complaint === true);",
  "ok('экспорт → импорт: метрики не поехали', totals(back.map(enrich)).hot === t.hot && totals(back.map(enrich)).margin === t.margin);",
  "ok('чужой CSV: разобран (2 строки)', f.length === 2, f.length);",
  "ok('чужой CSV: запятая внутри кавычек не рвёт поле', f[0].client === 'ООО Ромашка, отдел', f[0].client);",
  "ok('чужой CSV: сумма с десятичной запятой', f[0].amount === 1250000.5, f[0].amount);",
  "ok('чужой CSV: дата dd.mm.yyyy распознана', f[1].planDate === '2026-11-05', f[1].planDate);",
  "ok('чужой CSV: «yes» = рекламация', f[0].complaint === true);",
  "ok('незнакомая стадия добавляется 7-й колонкой', stagesInUse().indexOf('Unknownstage') === 6, stagesInUse().join(' / '));",
  "ok('незнакомая стадия получает норму 7 дней', enrich(f[1]).sla === 7, enrich(f[1]).sla);",
  "state.onlyHot = true;",
  "ok('фильтр «только с проблемой»', visibleRows().length === 9, visibleRows().length);",
  "state.onlyHot = false; state.manager = 'А. Морозова';",
  "ok('фильтр по менеджеру', visibleRows().length === 4, visibleRows().length);",
  "state.manager = ''; state.dealer = 'Салон Казань';",
  "ok('фильтр по дилеру', visibleRows().length === 3, visibleRows().length);",
  "state.dealer = ''; state.q = 'ниша 2400';",
  "ok('поиск по комментарию', visibleRows().length === 1, visibleRows().length);",
  "state.q = 'нет такого';",
  "ok('поиск без совпадений', visibleRows().length === 0, visibleRows().length);",
  "state.q = ''; state.stage = 'Производство';",
  "ok('фильтр по стадии', visibleRows().length === 3, visibleRows().length);",
  "console.log('');",
  "console.log(fails ? 'ПРОВАЛЕНО: ' + fails : 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ');",
  "return fails;"
].join('\n');

global.FOREIGN = foreign;
var failures = new Function(core + '\n' + 'var FOREIGN = global.FOREIGN;\n' + tests + '\n})();')();

process.exit(failures ? 1 : 0);
