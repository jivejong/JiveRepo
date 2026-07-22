/**
 * importer/extract.js
 *
 * Extracts raw text from a DOCX, preserving heading structure.
 * Mammoth strips Word's heading styles in raw text mode, so we use
 * HTML mode and convert <h1> → # prefix before handing to the parser.
 *
 * Also handles two-column layouts (Word table columns → sequential text).
 */

'use strict';

const mammoth = require('mammoth');

/**
 * Convert a DOCX file to parser-ready plain text.
 * h1 elements become # lines (song headers).
 * Returns { text, warnings }
 */
async function extractFromDocx(filePath) {
  const result = await mammoth.convertToHtml({ path: filePath });

  const warnings = result.messages.filter(m => m.type === 'warning');
  const html = result.value;

  // Convert HTML → parser-ready text
  const text = htmlToChartText(html);

  return { text, warnings };
}

/**
 * Convert mammoth HTML output to chart text format.
 * Rules:
 *   <h1>…</h1>  → # …        (song header)
 *   <h2>…</h2>  → ## …       (section header, treated as plain section)
 *   <p>…</p>    → line\n
 *   <br>        → \n
 *   <table>     → each <td> block extracted sequentially (two-column support)
 *   All other tags stripped
 */
function htmlToChartText(html) {
  const lines = [];

  // Process table cells first — two-column charts come in as tables
  // Each <td> becomes a block of lines, cells processed left-to-right top-to-bottom
  // For a two-column chart this means left column then right column, which is correct
  // reading order for setlist purposes
  const tableHandled = html.replace(/<table[\s\S]*?<\/table>/gi, (tableHtml) => {
    // Extract all td content blocks in DOM order
    const cells = [];
    const tdRe = /<td[\s\S]*?>([\s\S]*?)<\/td>/gi;
    let m;
    while ((m = tdRe.exec(tableHtml)) !== null) {
      cells.push(stripTags(m[1]).trim());
    }
    return cells.join('\n') + '\n';
  });

  // Now process remaining block elements line by line
  const blockRe = /<(h1|h2|h3|p|br|li)([\s\S]*?)>([\s\S]*?)<\/\1>|<br\s*\/?>/gi;
  let lastIndex = 0;
  let match;

  // Simpler approach: split on block boundaries
  const normalized = tableHandled
    // h1 → # prefix
    .replace(/<h1[^>]*>([\s\S]*?)<\/h1>/gi, (_, inner) => `\n# ${stripTags(inner).trim()}\n`)
    // h2/h3 → treat as plain section label
    .replace(/<h[23][^>]*>([\s\S]*?)<\/h[23]>/gi, (_, inner) => `\n${stripTags(inner).trim()}\n`)
    // paragraphs → lines
    .replace(/<p[^>]*>([\s\S]*?)<\/p>/gi, (_, inner) => `${stripTags(inner).trim()}\n`)
    // line breaks
    .replace(/<br\s*\/?>/gi, '\n')
    // strip remaining tags
    .replace(/<[^>]+>/g, '')
    // decode common HTML entities
    .replace(/&amp;/g,  '&')
    .replace(/&lt;/g,   '<')
    .replace(/&gt;/g,   '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g,  "'")
    .replace(/&nbsp;/g, ' ')
    // collapse 3+ consecutive blank lines to 2
    .replace(/\n{3,}/g, '\n\n');

  return normalized.trim();
}

function stripTags(html) {
  return html.replace(/<[^>]+>/g, '').trim();
}

module.exports = { extractFromDocx, htmlToChartText };
