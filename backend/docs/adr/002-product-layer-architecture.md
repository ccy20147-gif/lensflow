# ToonFlow Foundation — ADR-002: Product Layer Architecture

## Status
Approved (Foundation)

## Context
ToonFlow needs four distinct layers: Main Workflow, Agent, Media Recipe, and Workbench.
Confusing these layers would let agents modify resources, recipes call agents, or workbenches bypass the compiler.

## Decisions
1. **Call Matrix** (strictly enforced):

   | Caller | Can call | Cannot call |
   |--------|----------|-------------|
   | Main Workflow | Registered nodes, fixed Agent/Recipe, limited Subworkflow, WorkbenchTask | Recursive subworkflow, runtime latest, arbitrary code |
   | Agent | Approved models + ToolInvocation, bounded SOP, RequestInput | Other Agents, Workflow, Subworkflow, Recipe, Human Gate, WorkbenchTask, ResourceCommit |
   | Media Recipe | Media operators, providers, transforms, scoring | Agent, Workflow, Recipe nesting, Human Gate, RequestInput, arbitrary code |
   | Workbench | Read fixed Revision, edit Draft, submit Revision, request runs | Directly write Run/NodeRun, bypass compiler to call providers |

2. **WorkbenchTask**: Owned by Workflow; carries input snapshot, target workbench, typed output, base_revision/draft_version, commit strategy
3. **ManagedAgentTaskPlan**: Compiler-generated expansion of managed agent cards into explicit AgentInvoke + RequestInput + WorkbenchTask + HumanGate + ResourceCommit steps
4. **Agent output**: Only ArtifactVersion; no ResourceDraft/Revision writes
5. **Resource Revision**: Only frozen via CAS by Workflow/Workbench; agents cannot commit resources

## Consequences
- Each layer's API surface is documented and automatically tested
- New workbenches register routes and schemas without modifying the main canvas
- Cross-layer calls record revision, input, trace, cost, and safe errors
