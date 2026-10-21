// Synthetic events only. No model, tool, provider, or user data is executed/read.
// Usage: node --experimental-strip-types scripts/generate_subagents_fixture.mjs /path/to/pinned/weasley-subagents
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const revision = '72901e3f17eea4d56418b24e7a63f10c7ae22375';
const source = resolve(process.argv[2]);
// Permit a newer checkout only if tracked source is byte-identical to the pin.
for (const p of ['src/shared', 'src/runs/shared/parallel-utils.ts', 'src/agents/skills.ts']) {
  execFileSync('git', ['diff', '--exit-code', revision, '--', p], { cwd: source, stdio: 'pipe' });
}
const { createChildTranscriptWriter } = await import(pathToFileURL(resolve(source, 'src/shared/child-transcript.ts')));
const { getFinalOutput } = await import(pathToFileURL(resolve(source, 'src/shared/utils.ts')));
const dest = resolve(dirname(fileURLToPath(import.meta.url)), '../examples/subagents/synthetic.transcript.jsonl');
const originalNow = Date.now;
Date.now = () => 0;
try {
  const writer = createChildTranscriptWriter({ transcriptPath: dest, source: 'foreground',
    runId: 'synthetic-source-run', agent: 'synthetic-reader', childIndex: 0, cwd: '/synthetic' });
  writer.writeInitialUserMessage('Read the synthetic note and report its contents.');
  writer.writeChildEvent({type:'message_end', message:{role:'assistant', stopReason:'toolUse',
    content:[{type:'text',text:'Reading the fixture.'}, {type:'toolCall',id:'call-1',name:'read',arguments:{path:'synthetic-note.txt'}}]}});
  writer.writeChildEvent({type:'tool_execution_start',toolCallId:'call-1',toolName:'read',args:{path:'synthetic-note.txt'}});
  writer.writeChildEvent({type:'tool_execution_end',toolCallId:'call-1',toolName:'read',isError:false});
  writer.writeChildEvent({type:'tool_result_end',message:{role:'toolResult',toolCallId:'call-1',toolName:'read',isError:false,
    content:[{type:'text',text:'synthetic note'}]}});
  const terminal = {role:'assistant',stopReason:'stop',
    content:[{type:'text',text:'synthetic'},{type:'text',text:'note'}]};
  writer.writeChildEvent({type:'message_end',message:terminal});
  // Raw transcript projection deliberately differs from runtime finalOutput.
  assert.equal(getFinalOutput([terminal]), 'note');
  const records = readFileSync(dest, 'utf8').trimEnd().split('\n').map(JSON.parse);
  assert.equal(records.at(-1).text, 'synthetic\nnote');
  if (writer.getError()) throw new Error(writer.getError());
} finally { Date.now = originalNow; }
