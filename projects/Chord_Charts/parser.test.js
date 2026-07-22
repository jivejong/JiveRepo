'use strict';
const { parseChart, classifyLine, T } = require('./parser.js');

let passed = 0, failed = 0;

function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`  ${ok ? '✓' : '✗'} ${label}`);
  if (!ok) console.log(`      got:      ${JSON.stringify(actual)}\n      expected:  ${JSON.stringify(expected)}`);
  ok ? passed++ : failed++;
}

// ---------------------------------------------------------------------------
// 1. Header parsing
// ---------------------------------------------------------------------------
console.log('\n── Header variations ──');
const { parseHeader } = require('./parser.js');

let h = parseHeader('# Already Gone – D (E)\tEagles');
check('title',              h.title,              'Already Gone');
check('artist',             h.artist,             'Eagles');
check('chartWrittenKey',    h.chartWrittenKey,    'D');
check('soundingKeyFromHeader', h.soundingKeyFromHeader, 'E');
check('originalKey (none)', h.originalKey,        null);

h = parseHeader('# All I Want Is You – G\t\tU2\tAb');
check('title',              h.title,              'All I Want Is You');
check('artist',             h.artist,             'U2');
check('chartWrittenKey',    h.chartWrittenKey,    'G');
check('originalKey tab',    h.originalKey,        'Ab');

h = parseHeader('# American Girl – D\t\tTom Petty');
check('title',              h.title,              'American Girl');
check('artist',             h.artist,             'Tom Petty');
check('chartWrittenKey',    h.chartWrittenKey,    'D');
check('originalKey (none)', h.originalKey,        null);

h = parseHeader('# Angel Eyes – G (B)\t\tJeff Healey\tC');
check('title',              h.title,              'Angel Eyes');
check('artist',             h.artist,             'Jeff Healey');
check('soundingKeyFromHeader', h.soundingKeyFromHeader, 'B');
check('originalKey C',      h.originalKey,        'C');

h = parseHeader('# Back Where You Belong – G\t38 Special');
check('title',              h.title,              'Back Where You Belong');
check('artist',             h.artist,             '38 Special');
check('chartWrittenKey',    h.chartWrittenKey,    'G');

// ---------------------------------------------------------------------------
// 2. BB meta parsing
// ---------------------------------------------------------------------------
console.log('\n── BB meta variations ──');
const { parseBBMeta } = require('./parser.js');

let bb = parseBBMeta('BB: Intro / VS-CH / Modulation\t\tCapo 2');
check('structure',          bb.beatbuddyStructure, 'Intro / VS-CH / Modulation');
check('capo',               bb.defaultCapo,        2);
check('bpm (none)',         bb.bpm,                null);

bb = parseBBMeta('BB: VS / BR / End\t\tD-114');
check('structure',          bb.beatbuddyStructure, 'VS / BR / End');
check('bpm 114',            bb.bpm,                114);
check('capo (none)',        bb.defaultCapo,        0);

// ---------------------------------------------------------------------------
// 3. Line classifier
// ---------------------------------------------------------------------------
console.log('\n── Line classifier ──');
check('[CHORUS] → SECTION_REF',
  classifyLine('[CHORUS]').type, T.SECTION_REF);
check('[Intro] G D C → SECTION_WITH_CHORDS',
  classifyLine('[Intro] G    Cadd9    G    Cadd9').type, T.SECTION_WITH_CHORDS);
check('[Solo] G D  4X → SECTION_WITH_CHORDS',
  classifyLine('[Solo] G    Cadd9    G    Cadd9\t4X').type, T.SECTION_WITH_CHORDS);
check('[Outro] G D C  12X end on G → SECTION_WITH_CHORDS',
  classifyLine('[Outro]G    Cadd9    G    Cadd9  12X end on G').type, T.SECTION_WITH_CHORDS);
check('[Bridge] → SECTION_REF',
  classifyLine('[Bridge]').type, T.SECTION_REF);
check('[Pre-Chorus 1] → SECTION_REF',
  classifyLine('[Pre-Chorus 1]').type, T.SECTION_REF);
check('{D}Well she was → INLINE_CHORD_LYRIC',
  classifyLine('{D}Well she was an {E7}American girl.').type, T.INLINE_CHORD_LYRIC);
check('tab line → TAB',
  classifyLine('e--5-2-0-3-2-0-5-2-0-3-2-0-|--repeat--|').type, T.TAB);
check('tab b line → TAB',
  classifyLine('b--------------------------|---------|').type, T.TAB);
check('Whole step up → MODULATION',
  classifyLine('Whole step up').type, T.MODULATION);
check('1/2 step up → MODULATION',
  classifyLine('1/2 step up').type, T.MODULATION);
check('Verse 1 → PLAIN_SECTION',
  classifyLine('Verse 1').type, T.PLAIN_SECTION);
check('Chorus → PLAIN_SECTION',
  classifyLine('Chorus').type, T.PLAIN_SECTION);
check('Am I right? → LYRIC (not chord)',
  classifyLine('Am I right?').type, T.LYRIC);
check('D A G → CHORD',
  classifyLine('D A G').type, T.CHORD);
check('Cadd9 chord token',
  classifyLine('G       Cadd9    G                      Cadd9').type, T.CHORD);

// ---------------------------------------------------------------------------
// 4. Full song parse — Already Gone
// ---------------------------------------------------------------------------
console.log('\n── Full parse: Already Gone ──');
const alreadyGone = `# Already Gone – D (E)\tEagles 

BB: Intro / VS-CH / Modulation\t\tCapo 2\t

D A G 

        D                 A                G

Well, I heard some people talkin just the other day

[CHORUS]

Well I know it wasnt you who held me down

[CHORUS]

        G D     C

Yes I m already gone`;

const ag = parseChart(alreadyGone);
check('title',              ag.title,              'Already Gone');
check('artist',             ag.artist,             'Eagles');
check('chartWrittenKey',    ag.chartWrittenKey,    'D');
check('soundingKey',        ag.soundingKey,        'E');
check('defaultCapo',        ag.defaultCapo,        2);
check('bpm null',           ag.bpm,                null);
check('beatbuddyStructure', ag.beatbuddyStructure, 'Intro / VS-CH / Modulation');
check('has sections',       ag.sections.length > 0, true);
check('has CHORUS ref',     ag.sections.some(s => s.ref === '[CHORUS]'), true);

// ---------------------------------------------------------------------------
// 5. Full song parse — American Girl (BPM, inline chords)
// ---------------------------------------------------------------------------
console.log('\n── Full parse: American Girl ──');
const americanGirl = `# American Girl – D\t\tTom Petty

BB: VS / BR / End\t\tD-114

{D}Well she was an {E7}American girl. 
{G}Raised on {A}promises

Chorus

{G}Oh yeah,{A} allright, {D}take it easy baby

D                                     E7
e--5-2-0-3-2-0-5-2-0-3-2-0-|--repeat-|-7-4-0-5-4-0-7-4-0-5-4-0-|repeat--|
b--------------------------|---------|----------------------------------|`;

const amg = parseChart(americanGirl);
check('title',              amg.title,             'American Girl');
check('artist',             amg.artist,            'Tom Petty');
check('bpm 114',            amg.bpm,               114);
check('originalKey null',   amg.originalKey,       null);
check('has inline chords',  amg.sections.some(s =>
  s.lines && s.lines.some(l => l.type === 'inline_chord_lyric')), true);
check('has tab block',      amg.sections.some(s =>
  s.lines && s.lines.some(l => l.type === 'tab')), true);
check('has PLAIN_SECTION Chorus', amg.sections.some(s =>
  s.label && s.label.toLowerCase().includes('chorus')), true);

// ---------------------------------------------------------------------------
// 6. Section with chords
// ---------------------------------------------------------------------------
console.log('\n── Section with chords ──');
const introLine = classifyLine('[Intro] G    Cadd9    G    Cadd9');
check('label',   introLine.label,   '[Intro]');
check('chords',  introLine.chords,  'G Cadd9 G Cadd9');
check('repeat',  introLine.repeat,  null);

const soloLine = classifyLine('[Solo] G    Cadd9    G    Cadd9\t4X');
check('solo repeat', soloLine.repeat, '4X');

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
console.log(`\n${'─'.repeat(40)}`);
console.log(`${passed} passed, ${failed} failed out of ${passed + failed} checks`);
if (failed > 0) process.exit(1);
