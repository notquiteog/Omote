"""Your money, as a local ledger.

`store.py` is the whole of it: accounts, transactions, categories, budgets
and goals, in SQLite under the data directory. Nothing leaves the machine.

This is deliberately *not* a bank connector. Plaid and TrueLayer are real
products and either would work; a correct guess at one is a system that can
read a current account, and a wrong guess is a system that cannot be fixed by
the person who trusted it. `hr/store.py` gives the same answer for BambooHR
and Gusto, and says why at greater length.

Read-only by construction: there is no transfer, no payment and no route from
here to moving money. An agent that can read money and an agent that can move
money are different tools with different consequences, and a tool that does
both is a tool whose read is worth attacking.
"""
