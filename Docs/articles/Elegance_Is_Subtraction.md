# Elegance Is Subtraction

I am rarely impressed by lines of code. Anyone can write a lot of them. What impresses me is the engineer who takes a hard problem and solves it with almost nothing: a short function that does the whole job, one you read once and think could not have been written any other way. That is elegance, and I think AI is wearing it down.

Not because AI writes broken code. It writes code that works, and that is the trouble. Ask for a function and you will often get the function plus guard clauses for inputs that cannot occur, helper abstractions with a single caller, a test suite, and a docstring longer than the logic. Each line is defensible. The whole is inflated. Elegance is the discipline of taking things away, and AI’s default direction is the opposite one: keep adding until nothing can be criticized.

Someone told me recently that AI makes you more of the engineer you already are. I disagree, and I am my own counterexample. I have never been a verbose coder. I write lean code, I write few tests, and I never did much documentation. AI changed the documentation part completely; there I am a different developer than I was. The tool did not amplify my style. It pulled me toward its own.

## What the research shows, and what it does not

This is not one engineer’s grumbling. In May 2026, researchers at Concordia University published a study of AI-generated code with a finding that should bother anyone who assumed smarter models would write cleaner code: as models become more capable, their output becomes more bloated and more tightly coupled. In the multi-agent part of the study, total lines of code tracked architectural problems with a Spearman correlation of 0.94. Code that worked did not escape the decay, and neither did code produced from more detailed prompts.

Now the limits, because they matter. That 0.94 comes from five application scenarios built by one multi-agent framework running on one model. It is a small sample, and a correlation is not a cause. The paper also never tested the instruction I would have bet on, which is simply telling the model to be shorter. The authors guess that an explicit limit, such as a maximum function length, would help. They offer it as a hypothesis, not a result.

So does “write this in as few lines as possible” work? I will not claim it fails, because I have not seen it measured. But I would not bet on it, and the reason is that it is the wrong instruction. Line count is a proxy for elegance, not elegance itself. Squeeze a function into fewer lines and the easy way to comply is to pack the same logic tighter: code golf, dense and clever and harder to read than before. Elegance means fewer ideas to hold in your head, and a line count cannot see ideas.

Nor is this hopeless. Maxim Saplin pointed an AI agent at a Flutter app built entirely with AI assistance and cut about a third of its lines, 19,772 down to 13,509, with the test suite still green. It took a custom skill he wrote to stop the agent from gaming the line count, and he reports that it needed steady correction along the way.

My own numbers point the same direction. I ran a trimmer pass over AI-generated code, checking it against the project’s requirements. The first commit removed 30 files and 14,000 lines, mostly edge-case tests for situations that were never going to happen. Every one of those lines was defensible, and none of them was doing any work.

Bloat is not a law of nature. Deletion is a job: someone has to ask for it, specify it, and check it.

## What elegance actually is

Antoine de Saint-Exupéry, who flew airplanes before he wrote about them, set the standard for design in a single sentence in Wind, Sand and Stars: you are finished not when there is nothing left to add, but when there is nothing left to take away. I have not found a better definition of elegant code. It is what remains after the ego and the excess are gone, and it reads as if it could only have been written one way. A hundred lines of brute force and ten elegant ones can solve the same problem. The ten are not the point. The point is the leap that made the other ninety unnecessary.

Elegant code also outlives its moment. A friend of mine, a brilliant astronomer who worked for NASA, wrote a ray tracing program on a legacy system. The whole thing fit on a single 1.44 megabyte floppy disk, and a run took days. Many years later someone needed it. He loaded the old disk onto a newer machine and it ran in seconds instead of days. He told me it was the proudest moment of his career, because the code he had written all those years earlier had scaled perfectly.

Be clear about what did the speeding up. The hardware did, and he would be the first to say so. What the code did was let the hardware help: it was plain and self-contained enough that a machine it had never seen could run it, and run it faster. We usually measure scalability by how many users a service can carry at once. His story is about scale along another axis, which is time.

Elegant code is also written for the next reader, who is often you in six months. It does not need a five-page wiki to explain one function. It does not hide behind clever tricks. You can change one feature without breaking three unrelated systems. Clarity is not a courtesy here. It is how the code survives.

## Bare metal is not elegance

There is a persistent confusion in engineering that the code closest to the hardware is the purest and most serious code. Sometimes it is also necessary. Firmware and high-frequency trading systems live down there for good reason, and the people who write them are doing real work. But necessary is not the same as elegant.

Bare-metal optimization pays for speed with legibility. I came up on a Commodore 64, which is about as close to the metal as a machine gets, and I can attest that looking through machine code and hexadecimal is hard to read and harder to navigate. You get cryptic names, loops unrolled into walls of text, and comments that exist because the code cannot explain itself. It answers the question of how fast. Elegance answers the question of how clearly, and the two agree less often than people think. You can write beautiful Python and tangled C, and the reverse, so the language was never the measure.

This is the same trap as the “fewest lines” prompt. Compression and proximity to the machine are both easy to see and easy to admire, and neither is the thing. Performance is a measurement of the machine. Elegance is a judgment about the reader. Sometimes the most elegant move is to step away from the metal on purpose and write ten readable lines against the standard library, because that is the size the problem actually was.

## Elegance in AI implementation

The same standard applies to how we use AI itself, and here the argument turns from complaint to advice. The bloat is partly the models’ habit and partly ours. Reach for the biggest model for every task and you inherit the biggest model’s manners: comprehensive, defensive, generous with words.

The elegant order of operations has three steps. First, if the rules are absolute, write deterministic code. A regular expression that parses a date is faster and cheaper than any model, and it gives the same answer tomorrow that it gave today. Second, when the problem really does involve ambiguity, such as judgment, fuzzy classification, or language that no rule captures, use a model, and use the smallest one that does the job. A small model with a narrow task and a structured output is easier to test, cheaper to run, and has less room to wander. Third, save the large model for the genuine unknowns, where its breadth is the thing you are paying for.

One tempting claim I would leave out: that a small model is elegant because it lacks the capacity to overthink. Small models are not immune to bad output. The elegance is in the fit and the constraint, not in the model’s limits.

## Subtraction is a decision

Put it together and the pattern is plain. AI adds by default. The research shows it, and my own documentation habits show it. Elegance subtracts, and subtraction is a decision, which means someone has to make it: what to delete, what to hand to plain code, which tool gets which job. A model can help carry out the cut once someone has specified it, as the Saplin experiment shows. It does not volunteer the cut. That part stays with the engineer, and I would argue it was always the job.

If you have seen an AI produce something truly minimal, or you have a before and after I should look at, I would like to see it.

### Sources

- Zhu, Tsantalis and Rigby, [AI-Generated Smells: An Analysis of Code and Architecture in LLM- and Agent-Driven Development](https://arxiv.org/html/2605.02741v1), arXiv:2605.02741, May 2026
- Maxim Saplin, [Debloating The AI-Grown Codebase](https://dev.to/maximsaplin/debloating-the-ai-grown-codebase-2om), DEV Community, June 2026
- Antoine de Saint-Exupéry, Terre des hommes (1939), published in English as Wind, Sand and Stars
