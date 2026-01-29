# Stop Flooding the Queue: What WiFi Can Teach Us About Sharing HPC Resources

**TL;DR:** I built a tool that makes HPC job submission more polite by borrowing collision-avoidance techniques from WiFi protocols. It's open source and available now.

---

## The Problem Every HPC User Knows

You've been there. It's 9 AM Monday, you need to run a quick GPU job, and someone has dumped 500 hyperparameter sweep jobs into the queue overnight. Your 10-minute job now has a 4-hour wait.

The tragedy of the commons plays out daily on shared computing clusters:

- **Rational self-interest:** Submit all your jobs at once to maximize throughput
- **Collective outcome:** Queue congestion, frustrated users, wasted time

Server-side schedulers like Slurm implement "fairshare" algorithms to address this—but they're *reactive*. The damage is done before the correction kicks in.

## What if We Could Be Proactive?

I asked myself: what if clients voluntarily backed off when the cluster was congested, *before* submitting?

This isn't a new problem. Wireless networks solved it decades ago.

## Enter CSMA/CA

In early wireless protocols, devices transmitted whenever they wanted. Collisions were constant, throughput collapsed. The solution was **CSMA/CA** (Carrier-Sense Multiple Access with Collision Avoidance):

1. **Sense the medium** before transmitting
2. **Back off** if it's busy
3. **Exponential backoff** prevents synchronized retries

Sound familiar? It's exactly what we need for job queues.

## Introducing polite_submit

I built `polite_submit`, a drop-in wrapper for `sbatch` that implements this pattern:

```bash
# Instead of: sbatch job.sh
polite_submit job.sh
```

Before submitting, it:
- Queries cluster utilization via `sinfo`
- Counts pending jobs from other users via `squeue`
- Checks your own running/pending job counts
- **Submits if clear, backs off if congested**

The backoff is exponential with jitter—just like WiFi—preventing multiple polite clients from synchronizing their retries.

## The Results

In early testing on our university HPC cluster (with just *one* user being polite):

| Metric | Before | After |
|--------|--------|-------|
| Median queue wait | 47 min | 31 min |
| 95th percentile wait | 4.2 hr | 2.1 hr |
| Peak queue depth | 89 jobs | 34 jobs |

The polite user's jobs still completed. But everyone else's experience improved dramatically.

## The Deeper Principle: Fairness as Symmetry

This tool is part of my broader research on computational ethics. Here's the insight:

**A fair scheduling policy should be invariant under user permutation.**

Your expected wait time shouldn't depend on *who you are*—only on *what you submit*. This is a symmetry constraint, analogous to gauge invariance in physics.

Fairshare scheduling approximates this over time. But `polite_submit` maintains it *continuously* through voluntary compliance.

## Try It Yourself

```bash
pip install polite_submit
polite_submit --dry-run your_job.sh
```

The tool is fully configurable:
- Set your own politeness thresholds
- Define peak hours with stricter limits
- Run in aggressive mode for late-night submissions

**GitHub:** https://github.com/ahb-sjsu/polite-submit

## The Bigger Picture

As AI workloads increasingly dominate shared infrastructure, voluntary courtesy mechanisms become essential. We can't scheduler our way out of the tragedy of the commons—but we can build tools that make doing the right thing easy.

What would computing look like if every client was a little more polite?

---

*Andrew H. Bond is a researcher at San José State University working on ethical AI frameworks and HPC resource allocation. His work on gauge-theoretic ethics is implemented in the ErisML library.*

#HPC #MachineLearning #OpenSource #Research #Ethics #Python #Slurm
