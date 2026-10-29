// Opt-in synthetic subprocess integration, not a model/provider benchmark.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { chmodSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

assert.equal(process.platform === 'win32', false, 'synthetic child launcher requires POSIX');
const revision = '72901e3f17eea4d56418b24e7a63f10c7ae22375';
const source = resolve(process.argv[2]);
const mode = process.argv[3] ?? 'success';
assert.ok(['success', 'nonzero', 'incomplete'].includes(mode));
assert.equal(execFileSync('git', ['rev-parse', 'HEAD'], {cwd: source, encoding: 'utf8'}).trim(), revision);
// Refuse modified/untracked executable sources, not just a matching HEAD label.
assert.equal(execFileSync('git', ['status', '--porcelain', '--untracked-files=all', '--', 'src', 'package.json', 'package-lock.json'], {cwd: source, encoding: 'utf8'}), '');
assert.equal(execFileSync('git', ['ls-files', '--others', '--ignored', '--exclude-standard', '--', 'src'], {cwd: source, encoding: 'utf8'}), '');
const workspace = mkdtempSync(join(tmpdir(), 'agent-bench-synthetic-'));
try {
  // Isolate user configuration and eliminate provider credentials from child env.
  const keep = ['PATH', 'SystemRoot', 'WINDIR', 'PATHEXT', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR'];
  const environment = Object.fromEntries(keep.filter(k => process.env[k] !== undefined).map(k => [k, process.env[k]]));
  for (const key of Object.keys(process.env)) delete process.env[key];
  Object.assign(process.env, environment, {HOME: workspace, USERPROFILE: workspace,
    PI_CODING_AGENT_DIR: join(workspace, 'home'), WEASLEY_SUBAGENT_PI_BINARY: join(workspace, 'synthetic-child.mjs')});
  mkdirSync(join(workspace, 'home'));
  writeFileSync(join(workspace, 'synthetic-note.txt'), 'synthetic note', 'utf8');
  writeFileSync(process.env.WEASLEY_SUBAGENT_PI_BINARY, `#!/usr/bin/env node
import { readFileSync } from 'node:fs';
const emit = value => process.stdout.write(JSON.stringify(value) + '\\n');
const args = {path:'synthetic-note.txt'};
emit({type:'message_end', message:{role:'assistant',stopReason:'toolUse',model:'synthetic/local',content:[{type:'toolCall',id:'call-1',name:'read',arguments:args}]}});
emit({type:'tool_execution_start',toolCallId:'call-1',toolName:'read',args});
const text = readFileSync(args.path, 'utf8');
emit({type:'tool_execution_end',toolCallId:'call-1',toolName:'read',isError:false});
if (${JSON.stringify(mode)} !== 'incomplete') emit({type:'tool_result_end',message:{role:'toolResult',toolCallId:'call-1',toolName:'read',isError:false,content:[{type:'text',text}]}});
emit({type:'message_end',message:{role:'assistant',stopReason:'stop',model:'synthetic/local',content:[{type:'text',text}]}});
process.exitCode = ${mode === 'nonzero' ? 7 : 0};
`, 'utf8');
  chmodSync(process.env.WEASLEY_SUBAGENT_PI_BINARY, 0o700);
  const {runSync} = await import(pathToFileURL(join(source, 'src/runs/foreground/execution.ts')));
  const task = 'Read the synthetic note and report its contents.';
  const result = await runSync(workspace, [{name:'synthetic-reader',description:'Synthetic integration child',
    systemPrompt:'',systemPromptMode:'replace',inheritProjectContext:false,inheritSkills:false,model:'synthetic/local'}],
    'synthetic-reader', task, {runId:'synthetic-runtime',index:0,cwd:workspace,acceptance:false,
      artifactsDir:join(workspace,'artifacts'),timeoutMs:10000});
  assert.ok(result.artifactPaths?.metadataPath, 'runtime must persist metadata');
  assert.ok(result.transcriptPath, 'runtime must persist transcript');
  const metadata = JSON.parse(readFileSync(result.artifactPaths.metadataPath, 'utf8'));
  const transcript = readFileSync(result.transcriptPath, 'utf8');
  assert.equal(metadata.task, task);
  assert.equal(metadata.exitCode, result.exitCode);
  assert.equal(metadata.toolCount, 1);
  assert.equal(metadata.runId, 'synthetic-runtime');
  assert.equal(metadata.modelAttempts.length, 1);
  console.log(JSON.stringify({kind:'synthetic-runtime-integration', revision, mode, transcript, metadata,
    observed:{exitCode:result.exitCode,finalOutput:result.finalOutput,toolCount:result.progressSummary?.toolCount}}));
} finally {
  rmSync(workspace, {recursive:true,force:true});
}
