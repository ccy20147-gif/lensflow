import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const requirementsDir = path.join(root, "docs", "requirements");
const masterPath = path.join(
  root,
  "docs",
  "2026-07-12-toonflow-product-requirements-master.md",
);
const orderPath = path.join(
  root,
  "docs",
  "2026-07-12-toonflow-development-readiness-and-prd-order.md",
);
const trackerPath = path.join(
  root,
  "docs",
  "2026-07-12-toonflow-prd-delivery-tracker.md",
);

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

const allowedStates = new Set([
  "discovered",
  "defined",
  "reviewed",
  "approved",
  "in_delivery",
  "implemented",
  "verified",
  "released",
  "deferred",
  "superseded",
  "rejected",
]);

const expectedSections = [
  "元数据",
  "背景与问题",
  "目标与非目标",
  "用户与权限",
  "用户场景与主流程",
  "功能需求",
  "交互与展示",
  "数据、类型与公共接口",
  "状态机与业务规则",
  "失败、降级与恢复",
  "安全、隐私、内容与授权",
  "观测与运营",
  "验收标准",
  "测试场景",
  "交付与回退",
  "已决策事项与开放问题",
];

const failures = [];

// ---------------------------------------------------------------------------
// Validate third-party source ledger (TF-GOV-002)
// ---------------------------------------------------------------------------

const REQUIRED_LEDGER_COMPONENTS = [
  "Toonflow-app",
  "Toonflow-web",
  "SeedV",
  "Vue Flow",
  "WebAV",
];

const sourceLedgerPath = path.join(
  root,
  "docs",
  "third-party-source-ledger.md",
);

if (fs.existsSync(sourceLedgerPath)) {
  const ledger = fs.readFileSync(sourceLedgerPath, "utf8");
  for (const component of REQUIRED_LEDGER_COMPONENTS) {
    if (!ledger.includes(`### ${component}`) && !ledger.includes(`### ${component} `) && !ledger.includes(`### ${component}\n`)) {
      // Also try numbered format: "### 1. Toonflow-app"
      const numberedPattern = new RegExp(`### \\d+\\.\\s*${component.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`);
      if (!numberedPattern.test(ledger)) {
        failures.push(
          `third-party-source-ledger.md: missing required component "${component}"`,
        );
      }
    }
    const componentSection = ledger.split(`### ${component}`)[1]?.split("### ")[0] ?? "";
    if (componentSection && !componentSection.includes("**裁决**")) {
      failures.push(
        `third-party-source-ledger.md: component "${component}" is missing a decision field`,
      );
    }
    if (componentSection && !componentSection.includes("许可证 hash")) {
      failures.push(
        `third-party-source-ledger.md: component "${component}" is missing a license hash`,
      );
    }
  }
} else {
  failures.push(
    "third-party-source-ledger.md not found — required by TF-GOV-002",
  );
}

// ---------------------------------------------------------------------------
// Validate quality test suite (TF-QLT-001)
// ---------------------------------------------------------------------------

const REQUIRED_SUITES = [
  "文本套件",
  "身份套件",
  "镜头套件",
  "51 镜头",
  "广告图套件",
  "交互套件",
];

const qualitySuitePath = path.join(root, "docs", "quality", "foundation-test-suite.md");

if (fs.existsSync(qualitySuitePath)) {
  const suite = fs.readFileSync(qualitySuitePath, "utf8");
  for (const requiredSuite of REQUIRED_SUITES) {
    if (!suite.includes(requiredSuite)) {
      failures.push(
        `foundation-test-suite.md: missing required test suite "${requiredSuite}"`,
      );
    }
  }
  if (!suite.includes("Critical Failure")) {
    failures.push("foundation-test-suite.md: missing Critical Failure rules");
  }
} else {
  failures.push("foundation-test-suite.md not found — required by TF-QLT-001");
}

const requirementFiles = fs
  .readdirSync(requirementsDir)
  .filter((file) => file.endsWith(".md"))
  .sort();

function field(lines, label) {
  const bullet = lines.find((line) => line.startsWith(`- ${label}：`));
  if (bullet) return bullet.slice(`- ${label}：`.length).trim();

  const table = lines.find((line) => line.startsWith(`| ${label} |`));
  if (table) return table.split("|")[2]?.trim() ?? "";
  return "";
}

function metadata(lines, primary, fallback) {
  return field(lines, primary) || (fallback ? field(lines, fallback) : "");
}

function continuousNumbers(text, prefix) {
  return [...text.matchAll(new RegExp(`^- ${prefix}-(\\d+)`, "gm"))].map(
    (match) => Number(match[1]),
  );
}

function assertContinuous(file, label, numbers) {
  if (numbers.length === 0) {
    failures.push(`${file}: missing ${label} entries`);
    return;
  }
  numbers.forEach((number, index) => {
    if (number !== index + 1) {
      failures.push(`${file}: ${label} numbering is not continuous at ${number}`);
    }
  });
}

const records = new Map();
for (const file of requirementFiles) {
  const text = fs.readFileSync(path.join(requirementsDir, file), "utf8");
  const lines = text.split(/\r?\n/);
  const id = metadata(lines, "ID");
  const state = metadata(lines, "状态");
  const version = metadata(lines, "目标版本", "版本");
  const priority = metadata(lines, "优先级");
  const owner = metadata(lines, "责任域");
  const dri = metadata(lines, "个人 DRI");
  const dependencyLine = metadata(lines, "直接依赖");
  const dependencies = [...new Set(dependencyLine.match(/TF-[A-Z]+-\d+/g) ?? [])];
  const expectedId = file.replace(/\.md$/, "");

  if (!id || id !== expectedId) failures.push(`${file}: ID must equal ${expectedId}`);
  if (records.has(id)) failures.push(`${file}: duplicate ID ${id}`);
  if (!allowedStates.has(state)) failures.push(`${file}: invalid state ${state || "<empty>"}`);
  if (!version) failures.push(`${file}: missing version`);
  if (!priority) failures.push(`${file}: missing priority`);
  if (!owner) failures.push(`${file}: missing responsibility domain`);
  if (!dri) failures.push(`${file}: missing personal DRI field`);
  if (!dependencyLine) failures.push(`${file}: missing direct dependencies`);

  const sections = [...text.matchAll(/^## (\d+)\.\s*(.+)$/gm)].map((match) => ({
    number: Number(match[1]),
    title: match[2].trim(),
  }));
  const sectionNumbers = sections.map(({ number }) => number);
  if (
    sectionNumbers.length !== 16 ||
    sectionNumbers.some((number, index) => number !== index + 1)
  ) {
    failures.push(`${file}: expected sections 1..16 exactly once`);
  }
  sections.forEach(({ title }, index) => {
    if (expectedSections[index] && title !== expectedSections[index]) {
      failures.push(
        `${file}: section ${index + 1} must be ${expectedSections[index]}, got ${title}`,
      );
    }
  });

  assertContinuous(file, "FR", continuousNumbers(text, "FR"));
  assertContinuous(file, "AC", continuousNumbers(text, "AC"));

  records.set(id, { file, state, dri, dependencies });
}

for (const [id, record] of records) {
  for (const dependency of record.dependencies) {
    if (!records.has(dependency)) {
      failures.push(`${record.file}: unknown dependency ${dependency}`);
    }
    if (dependency === id) failures.push(`${record.file}: self dependency`);
  }
}

const visiting = new Set();
const visited = new Set();
function visit(id, stack) {
  if (visiting.has(id)) {
    failures.push(`dependency cycle: ${[...stack, id].join(" -> ")}`);
    return;
  }
  if (visited.has(id)) return;
  visiting.add(id);
  for (const dependency of records.get(id)?.dependencies ?? []) {
    if (records.has(dependency)) visit(dependency, [...stack, id]);
  }
  visiting.delete(id);
  visited.add(id);
}
for (const id of records.keys()) visit(id, []);

const master = fs.readFileSync(masterPath, "utf8");
const masterIds = new Set(
  [...master.matchAll(/^\| (TF-[A-Z]+-\d+) \|/gm)].map((match) => match[1]),
);
const masterStatuses = new Map();
for (const line of master.split(/\r?\n/)) {
  const cells = line
    .split("|")
    .slice(1, -1)
    .map((cell) => cell.trim());
  if (!/^TF-[A-Z]+-\d+$/.test(cells[0] ?? "")) continue;
  const state = cells.at(-1);
  if (!allowedStates.has(state)) continue;
  if (masterStatuses.has(cells[0])) {
    failures.push(`master requirement table duplicates ${cells[0]}`);
  }
  masterStatuses.set(cells[0], state);
}
for (const id of records.keys()) {
  if (!masterIds.has(id)) failures.push(`master table missing ${id}`);
  if (!masterStatuses.has(id)) failures.push(`master requirement row missing ${id}`);
  if (masterStatuses.get(id) && masterStatuses.get(id) !== records.get(id).state) {
    failures.push(
      `state mismatch for ${id}: master=${masterStatuses.get(id)}, detail=${records.get(id).state}`,
    );
  }
}
for (const id of masterIds) {
  if (!records.has(id)) failures.push(`master table has unknown ${id}`);
}

if (fs.existsSync(orderPath)) {
  const order = fs.readFileSync(orderPath, "utf8");
  for (const record of records.values()) {
    if (!order.includes(`\`${record.file}\``)) {
      failures.push(`development order missing ${record.file}`);
    }
  }
}

if (fs.existsSync(trackerPath)) {
  const tracker = fs.readFileSync(trackerPath, "utf8");
  const trackerRows = new Map();
  for (const line of tracker.split(/\r?\n/)) {
    const cells = line
      .split("|")
      .slice(1, -1)
      .map((cell) => cell.trim());
    const fileMatch = cells[0]?.match(/^`(TF-[A-Z]+-\d+\.md)`$/);
    if (!fileMatch) continue;
    const file = fileMatch[1];
    if (trackerRows.has(file)) failures.push(`delivery tracker duplicates ${file}`);
    trackerRows.set(file, {
      dri: cells[3],
      state: cells[4],
      completion: cells[5],
    });
  }

  for (const record of records.values()) {
    const row = trackerRows.get(record.file);
    if (!row) {
      failures.push(`delivery tracker missing ${record.file}`);
      continue;
    }
    if (row.state !== record.state) {
      failures.push(
        `delivery tracker state mismatch for ${record.file}: tracker=${row.state}, detail=${record.state}`,
      );
    }
    const detailDriPending = record.dri === "待指派";
    const trackerDriPending = row.dri === "待认领" || row.dri === "待指派";
    if (detailDriPending !== trackerDriPending) {
      failures.push(`delivery tracker DRI mismatch for ${record.file}`);
    }
    if (row.completion === "[x]" && !["verified", "released"].includes(row.state)) {
      failures.push(`${record.file}: completed marker requires verified or released`);
    }
    if (row.state === "deferred" && row.completion !== "N/A") {
      failures.push(`${record.file}: deferred tracker row must use N/A completion`);
    }
    if (row.state !== "deferred" && !["[ ]", "[x]"].includes(row.completion)) {
      failures.push(`${record.file}: invalid completion marker ${row.completion}`);
    }
  }
  for (const file of trackerRows.keys()) {
    if (!requirementFiles.includes(file)) {
      failures.push(`delivery tracker has unknown ${file}`);
    }
  }
}

if (failures.length > 0) {
  console.error(`Requirement validation failed (${failures.length}):`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

const stateCounts = {};
for (const { state } of records.values()) {
  stateCounts[state] = (stateCounts[state] ?? 0) + 1;
}
console.log(
  `Requirement validation passed: ${records.size} PRDs, no unknown dependencies or cycles.`,
);
console.log(`States: ${JSON.stringify(stateCounts)}`);
