## Load Bearing: A Transformation Framework for Legacy Platform Estates

10.5281/zenodo.23029653

This white paper introduces the Load Bearing Framework, a structured approach for integrating artificial intelligence into legacy platform estates (COBOL and mainframe cores, Java and C# enterprise systems, and DOS-era controllers governing physical machinery) without disturbing the structural role those platforms already play. It argues that the dominant modernization instinct, replacing a legacy platform before adding AI to it, inverts the platform's actual risk profile: replacement is reliably the more dangerous event, and AI integration is safer when routed through the existing load-bearing layer rather than around it.

The framework specifies seven disciplines, each addressing a failure mode observed in real migration and modernization programs: structural load assessment, deterministic core preservation, integration-over-replacement architecture, compounding migration risk, operator knowledge continuity, interface drift detection, and reinforcement timing. Evidence is drawn from banking (including the 2018 TSB Bank migration and Royal Bank of Canada's mainframe streaming architecture), government (the 2020 state unemployment system failures), airline operations (Southwest Airlines' 2022 meltdown), and empirical research on IT project cost overruns and power-law risk distributions.

A closing section shows that Microsoft (Agent Framework), the Spring team (Spring AI), and IBM (watsonx Code Assistant for Z) have independently converged on the same integration pattern: framework-native orchestration, type-safe structured output as the boundary discipline, and protocol standardization through the Model Context Protocol. The paper connects Java and C# to the trajectory COBOL has already walked. It is a companion to Forward Legacy: where that paper addresses the data estates enterprises have accumulated, this one addresses the platform estates that process them.

Full paper at [Zenodo](https://zenodo.org/records/23029653)
