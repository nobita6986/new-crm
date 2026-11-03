"""Deterministic weighted-fair allocator with an explicit, persistable state.

P1 §7.3 requires a cumulative deficit algorithm whose counters survive across
batches, and P1 §7.4 requires that 1.000 leads with weights 50/30/20 end up as
500/300/200 and that 100 single-lead batches still sum to 50/30/20.

The algorithm here is the integer form of the cumulative deficit rule:

    score(user) = next_total * weight(user) - assigned_count(user) * sum_weights

The highest score wins; ties are broken by the stable alphabetical order of the
user id, so the same input always produces the same output.

`state` is a plain dict so it can be persisted as JSON in
`CSKH Distribution Run.state_json`.

Two counter families live in the state, and keeping them apart is what makes the
capacity rule behave (ADR-P1-04 §2.5):

* **Epoch counters** — `epoch`, `total`, `counts`, `signature`. These drive the
    *fairness target*. When the eligible set changes (a user hits their
    capacity, a rule changes weight, a user is disabled) the epoch is bumped
    and the epoch counters restart, so the remaining users recompute their
    target for the new denominator instead of inheriting a stale "debt".
* **Cumulative ledger** — `allocated_total`, `allocated_counts`. These count
    **every** allocation ever made, across all epochs, and are never reset.
    They are the audit trail of what the allocator actually handed out ("user A
    received exactly 2 work items"), and they are what the dashboard and the
    reconciliation read.

An earlier draft reset `counts` together with the epoch, which silently lost
the ledger of a user that dropped out because it hit its capacity: the report
said the user received 0 items although the database held 2. The separation
below is the fix.

`loads` tracks the currently assigned active work items per user and is used
for capacity accounting (P1 §1.3: capacity counts active assigned work items,
not the historical total).
"""

from copy import deepcopy

MIN_WEIGHT = 1


class AllocationError(ValueError):
	"""Raised when the allocation inputs are invalid."""


def _validate_rules(rules):
	if not isinstance(rules, (list, tuple)):
		raise AllocationError("Allocation rules must be a list.")
	seen = set()
	validated = []
	for rule in rules:
		if not isinstance(rule, dict):
			raise AllocationError("Each allocation rule must be an object.")
		user = rule.get("user")
		if not isinstance(user, str) or not user:
			raise AllocationError("Each rule requires a distinct user.")
		if user in seen:
			raise AllocationError("Each rule requires a distinct user.")
		seen.add(user)
		weight = rule.get("weight")
		if type(weight) is not int or weight < MIN_WEIGHT:
			raise AllocationError("Rule weights must be positive integers.")
		capacity = rule.get("capacity", 0)
		if type(capacity) is not int or capacity < 0:
			raise AllocationError("Rule capacities must be non-negative integers.")
		enabled = rule.get("enabled", True)
		if not isinstance(enabled, bool):
			raise AllocationError("The enabled flag must be a boolean.")
		validated.append({"user": user, "weight": weight, "capacity": capacity, "enabled": enabled})
	return validated


def _validate_state(state):
	if state is None:
		return {}
	if not isinstance(state, dict):
		raise AllocationError("The allocator state must be a mapping.")
	clean = deepcopy(state)
	for key in ("epoch", "total", "allocated_total"):
		value = clean.get(key, 0)
		if type(value) is not int or value < 0:
			raise AllocationError("The allocator state counters must be non-negative integers.")
	for key in ("counts", "loads", "allocated_counts"):
		mapping = clean.get(key, {})
		if not isinstance(mapping, dict):
			raise AllocationError("The allocator state counters must be a mapping.")
		for value in mapping.values():
			if type(value) is not int or value < 0:
				raise AllocationError("Active loads must be non-negative integers.")
	return clean


def eligible_rules(rules, loads):
	"""Return the rules that may still receive a work item."""
	eligible = []
	for rule in rules:
		if not rule.get("enabled", True):
			continue
		capacity = rule.get("capacity", 0)
		if capacity and loads.get(rule["user"], 0) >= capacity:
			continue
		eligible.append(rule)
	return eligible


def signature(rules, loads):
	"""The stable fingerprint of the currently eligible user/weight pairs."""
	return [[rule["user"], rule["weight"]] for rule in eligible_rules(rules, loads)]


def _stable_tiebreak(user):
	# Sorting ascending by user is the tie-break. Negating the code points keeps
	# `max()` usable without reversing the alphabetical order.
	return [-ord(char) for char in user]


def allocate(count: int, rules: list[dict], state: dict | None = None, loads: dict | None = None) -> dict:
	"""Allocate `count` work items across `rules` and return the new state.

	`loads` is authoritative when supplied: it is the current number of active
	assigned work items per user. Without it, the loads carried inside `state`
	are used and extended in place.
	"""
	if type(count) is not int or count < 0:
		raise AllocationError("Allocation count must be a non-negative integer.")

	validated = _validate_rules(rules)
	next_state = _validate_state(state)

	current_loads = deepcopy(loads if loads is not None else next_state.get("loads", {}))
	if any(type(value) is not int or value < 0 for value in current_loads.values()):
		raise AllocationError("Active loads must be non-negative integers.")

	# Stable order: the tie-break must not depend on the database row order.
	ordered = sorted(validated, key=lambda rule: rule["user"])

	assignments = []
	for _ in range(count):
		eligible = eligible_rules(ordered, current_loads)
		if not eligible:
			break

		current_signature = [[rule["user"], rule["weight"]] for rule in eligible]
		if next_state.get("signature") != current_signature:
			# New epoch: the fairness **target** is recomputed for the new user
			# set. Only the epoch counters restart; the cumulative ledger
			# (`allocated_total` / `allocated_counts`) keeps every allocation
			# ever made, so a user that drops out because it hit its capacity
			# does not lose the record of what it received.
			next_state.update(
				signature=current_signature,
				epoch=next_state.get("epoch", 0) + 1,
				total=0,
				counts={rule["user"]: 0 for rule in eligible},
			)

		counts = next_state.setdefault("counts", {})
		next_total = next_state.get("total", 0) + 1
		weight_sum = sum(rule["weight"] for rule in eligible)

		winner = max(
			eligible,
			key=lambda rule: (
				next_total * rule["weight"] - counts.get(rule["user"], 0) * weight_sum,
				_stable_tiebreak(rule["user"]),
			),
		)["user"]

		counts[winner] = counts.get(winner, 0) + 1
		next_state["total"] = next_total

		# The cumulative ledger counts **every** allocation, in every epoch. It
		# is never reset, so it is the honest answer to "how many work items
		# did this user actually receive".
		allocated = next_state.setdefault("allocated_counts", {})
		allocated[winner] = allocated.get(winner, 0) + 1
		next_state["allocated_total"] = next_state.get("allocated_total", 0) + 1

		current_loads[winner] = current_loads.get(winner, 0) + 1
		assignments.append(winner)

	next_state["loads"] = current_loads
	return {
		"assignments": assignments,
		"unassigned": count - len(assignments),
		"state": next_state,
	}


def planned_distribution(count: int, rules: list[dict]) -> dict:
	"""Preview the expected split without touching any persisted state."""
	validated = _validate_rules(rules)
	ordered = sorted(validated, key=lambda rule: rule["user"])
	active = [rule for rule in ordered if rule.get("enabled", True)]
	weight_sum = sum(rule["weight"] for rule in active)
	planned = {}
	for rule in active:
		planned[rule["user"]] = int(count * rule["weight"] / weight_sum) if weight_sum else 0
	# Hand out the rounding remainder deterministically by the stable order.
	remainder = count - sum(planned.values())
	for rule in active:
		if remainder <= 0:
			break
		planned[rule["user"]] += 1
		remainder -= 1
	return {
		"eligible_count": count,
		"targets": [
			{"user": rule["user"], "weight": rule["weight"], "planned": planned[rule["user"]]}
			for rule in active
		],
	}
