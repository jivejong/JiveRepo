"""The model-call budget: one unit per HTTP attempt to the Gemini API, including a retry inside GeminiClient.generate.

Implements doc 06 "Runtime" (MAX_MODEL_CALLS_PER_RUN) and "Evaluation fixtures" (the eval suite cap). The client charges the budget
before every attempt, so the cap bounds real requests, not just the loop's model calls.
"""


class BudgetExhausted(Exception):
    """The run's call budget is spent. The caller leaves the incident unprocessed."""


class CallBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    @property
    def spent(self):
        return self.used >= self.limit

    def charge(self):
        """Take one unit, or raise BudgetExhausted when none is left."""
        if self.spent:
            raise BudgetExhausted(f"the call budget of {self.limit} is spent")
        self.used += 1
