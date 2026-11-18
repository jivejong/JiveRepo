# Think High, Write Low

_Right-sizing the model to the work, in code, infrastructure, and documentation_

The most capable model is not always the right model.

Point a frontier model at a validation function or a DTO and you rarely get just the utility you asked for. You get three interfaces, hypothetical scale, and edge cases that will never exist in your codebase. Then you spend longer pruning the pull request than you would have spent writing the code.

Most people file that under bad prompting. I'd argue it's bad routing.

## Reasoning is for deciding

Every task has two halves: working out what to do, and doing it. Heavy reasoning earns its cost in the first half. Once the decision is made, the second half is execution, and a more powerful engine brings nothing to it but ambition. Think high. Write low.

I apply that by routing on cognitive workload, not on how important the work feels. The same four rungs show up whether I'm writing code, infrastructure, or prose:

1. **Lightweight model.** The problem and the solution are already clear, and the blast radius is small.
2. **Daily driver.** The work needs real judgment, but the architecture is understood. Most professional work lives here.
3. **Higher reasoning.** Uncertainty needs investigation, or a mistake is expensive.
4. **Frontier mode.** An escalation path, never the default. The solution space is wide, the ambiguity is high, and the answer is hard to verify.

Start at the bottom and climb when the uncertainty or the consequence demands it. What counts as uncertainty and consequence differs by domain, and each domain teaches something the others don't.

## Code: the cost of ambition

High reasoning pays when the solution space is wide and the failure modes are subtle: an intermittent race condition across distributed services, a legacy modernization, an opaque dependency graph. That's work for the upper rungs. When the problem and the path are already clear, the same reasoning budget is friction. It's where the three interfaces come from.

So a DTO, a straightforward validation, a rename, or a test for defined behavior goes to the lightweight model. Normal feature work, multi-file changes, and API integrations go to the daily driver. Difficult debugging, large refactors, and dependency upgrades earn higher reasoning. Frontier mode is for the cases where several explanations are plausible, or where you suspect the architecture itself is wrong.

The corollary matters more than the list. When an agent overcomplicates a task, don't add a paragraph of keep-it-simple constraints to the prompt. Route the work to an engine that lacks the ambition to overthink it.

## Infrastructure: the diff lies

In application code, a ten-line PR is usually a bounded fix. In platform work, a two-line edit to an IAM policy, an ingress manifest, or a CIDR block can sever network access or open permissions across an entire cluster. The HCL or YAML that wipes out a database is trivial to write, and that's the trap. Simple syntax gets mistaken for simple work.

The most dangerous habit in infrastructure automation is judging risk by the size of the diff. Line count is the wrong proxy. Consequence is the real signal, and I read it through three questions: how complex is the change, how far can it spread, and how hard is it to undo.

This looks like a counterexample to “write low,” but it's the rule at its sharpest. The typing is trivial. The thinking is not, because the real question is what happens downstream, and that takes reasoning. So tag bumps, variables, and routine Helm values go to the lightweight model. Terraform modules, deployments, and CI/CD pipelines go to the daily driver. IAM, RBAC, networking, DNS, and Terraform state move up to deeper reasoning. Intermittent Kubernetes failures, service mesh problems, and cross-region outages go to the frontier.

The risk runs in both directions. The cheap model isn't dangerous because it's cheap. It's dangerous because it will happily write a syntactically valid security group rule that is also a disaster, since it can't simulate the downstream topology. And the frontier model on a tag bump just adds latency and drift. When the rollback is hard or impossible, deep reasoning isn't a luxury. It's your blast shield.

## Documentation: the same split, in prose

Writing and reasoning are two separate steps. Most teams run them as one.

Hand a technical spec or strategy brief to a frontier model in one shot and you usually get the reasoning budget spent finding the nuance, then the nuance buried under self-justifying prose. The model that solved the problem can't resist performing its intelligence on the page.

Documents rarely fail because the writing is unpolished. They fail because the decisions underneath were never reconciled: three teams with conflicting requirements for an API redesign, a postmortem built from contradictory logs and conflicting accounts. That's analytical work, and it's where frontier reasoning pays for itself. Paying that same model to turn the finished resolution into bullets or a runbook is a waste.

So I split the job. When the source already holds the answer, as with summaries, release notes, and cleanup, a lightweight model is enough. When the facts are known and someone has to organize them, as with READMEs, ADRs, runbooks, and proposals, the daily driver does it well. When the inputs conflict or the assumptions are hidden, which is postmortem and competing-requirements territory, the work wants deeper reasoning. When there's ambiguity and no known structure yet, that's frontier work.

If a document is hard to follow, a more powerful writer is almost never the fix. Use a reasoning model to figure out what you're trying to say, then a simpler one to say it plainly.

## Size the job to the box

The mainframe shops understood this. Capacity was finite, so the job got sized to the box. That constraint taught the discipline, and it applies to token budgets the same way.

Across all three domains the rule is the same: spend the reasoning where the decision is, and where a mistake would be expensive. Everywhere else, write low.
