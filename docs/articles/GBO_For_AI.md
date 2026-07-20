## MBO Is Seventy Years Old. It's Also the Best Framework for Governing AI Systems.

Enterprise AI has an unexpected problem.

It isn't just a technology problem. It's a governance problem.

Organizations are looking for the next framework, platform, philosophy, anything to manage AI agents. It's a place where cost savings and risk reduction are realized, and companies are scrambling to discover the new management approach for the AI era. 

What if the framework we need has been sitting on the shelf since 1954?

Peter Drucker introduced Management by Objectives in _The Practice of Management_ seventy years ago. The idea was precise: define a clear objective, establish measurable success criteria, give the responsible party the autonomy to determine how to achieve it, and evaluate based on outcomes rather than activity. Drucker wasn't describing a productivity hack. He was describing a governance philosophy — one that locates accountability in the objective, not the method.

I first encountered MBO as a business major in the 1990s. I thought it was a great concept at the time. However, I filed it where most people file management theory: useful in school, ornamental in practice. What I didn't expect was to spend thirty years in enterprise IT and arrive at the AI moment recognizing that Drucker had already described the governance problem we are now failing to solve.

The framework already exists. Many organizations in the public sector have adapted the management model to a governance model known as Governance by Objective. It is the deliberate translation of an established governance model to domains such as health care where the failures create negative impacts. What I'm proposing is GBO for AI Systems.

The translation is direct. In Drucker's model, you define the objective, establish the criteria for success, calibrate the autonomy of the responsible party, and evaluate outcomes. In GBO for AI Systems, you do exactly the same thing — except the responsible party is an agent, and the calibration of autonomy is not a personnel decision. It is an architectural one.

That distinction matters more than most organizations currently understand.

An AI agent is not a person. It does not need motivation, mentorship, or morale management. It does not respond to encouragement or suffer from unclear expectations the way a junior employee might. What it does reliably, at scale, without fatigue, is execute against the objective it was given. If the objective was poorly defined, the agent will execute against the poorly defined objective with complete fidelity and no awareness that anything has gone wrong. When you don't give an agent a clean objective, you get output that technically answers the question you asked while completely missing the problem you needed solved. When you don't define success criteria in advance, you evaluate output against an unstated standard and conclude the agent didn't understand. When you provide contradictory constraints: be concise, be thorough, follow our brand voice, include these forty-seven additional requirements — you should not be surprised when the output looks like it was written by a committee.

That is not the model failing. That is poor objective design. And poor objective design is a governance failure, not a technology failure.

Drucker identified the root cause of this pattern before the technology existed to produce it. He called it the activity trap: the tendency of organizations to optimize the process instead of the outcome, to measure what is easy to count rather than what actually matters. Enterprise AI implementations are falling into the same trap with remarkable consistency. Teams measure prompt volume, model usage, API calls, response time, and token consumption. Those metrics may matter operationally. But none of them answer the question that determines whether the deployment has value: did the agent achieve the objective it was deployed to solve? A system can execute perfectly within its parameters while drifting steadily away from its purpose (agentic drift). The solution is not more prompts. The solution is better objectives.

Most prompt engineering today is an attempt to compensate for poor objective engineering. The prompt is the interface. The objective is the foundation. Fix the foundation, and everything above it improves.

GBO for AI Systems operationalizes that principle across five disciplines.

- Define the objective with a precise measurable target with a defined threshold.
- Establish acceptance criteria before configuring the agent, because if you cannot describe what success and failure look like, you may not understand the process well enough to automate it.
- Define autonomy explicitly: what actions the agent can take, what requires human approval, and when it escalates.
- Calibrate autonomy progressively, starting with advisory functions and earning higher levels through demonstrated performance rather than granting it as a configuration default.
- Build continuous feedback loops because agents do not improve through encouragement. They improve through measurement, drift detection, and objective refinement at the speed of the system.

The autonomy question deserves particular attention because it is where the most consequential mistakes are being made. Autonomy is not a configuration setting. It is a governance decision, and it should be governed accordingly. We do not trust production systems because they exist. We trust them because they have demonstrated predictable behavior under observation. AI agents should meet the same standard. Start advisory. Move to semi-autonomous when performance justifies it. Grant higher levels of autonomy only when governance evidence supports it. Trust is not granted. Trust is earned.

Human oversight is not a temporary safeguard until AI becomes capable enough. It is the governance layer. Humans define objectives. Humans establish constraints. Humans evaluate outcomes. Humans decide when additional autonomy has been earned. The goal is not to remove humans from the process. The goal is to move humans from performing every task to governing systems that can perform tasks responsibly. That is what Drucker was describing in 1954. The technology is different. The workers are different. The governance problem is not.

GBO for AI Systems gives leadership something that most current AI deployments cannot provide: a structured basis for measuring ROI, comparing agent performance, tuning autonomy, and replacing intuition with documented governance criteria. That is not a secondary benefit. That is the business case.

Drucker created MBO because he saw organizations substitute activity for results and lose sight of purpose in the process. Seventy years later, we are doing it again — at scale, at speed, with systems that will execute against a bad objective indefinitely without complaint.

The framework to fix it is not new. It's timeless.
