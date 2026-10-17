"""Agent roles: worker, judge, nominator, pooling call, lens minter, goal minter, tagger, coordinator.

Roles never message each other; each reads its inputs from the stores and returns domain values the
round close writes. Every role calls models only through the `ModelClient` port.
"""
