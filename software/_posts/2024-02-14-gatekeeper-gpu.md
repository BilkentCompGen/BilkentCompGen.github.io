---
layout: software
title: GateKeeper-GPU
description: GPGPU design for accelerating pre-alignment in DNA short and long read mapping
image: /images/software/gkgpu.png
contributors: 
  - Zülal Bingöl
  - Mohammed Alser
year: 2024
repo: BilkentCompGen/GateKeeper-GPU
---

# GateKeeper-GPU: GPGPU design for accelerating pre-alignment in DNA short and long read mapping

At the last step of short read mapping, the candidate locations of the reads on the reference genome are verified to compute their differences from the corresponding reference segments using sequence alignment algorithms. Calculating the similarities and differences between two sequences is still computationally expensive since approximate string matching techniques traditionally inherit dynamic programming algorithms with quadratic time and space complexity. We introduce GateKeeper-GPU, a fast and accurate pre-alignment filter that efficiently reduces the need for expensive sequence alignment. GateKeeper-GPU provides two main contributions: first, improving the filtering accuracy of GateKeeper (a lightweight pre-alignment filter), and second, exploiting the massive parallelism provided by the large number of GPU threads of modern GPUs to examine numerous sequence pairs rapidly and concurrently. By reducing the work, GateKeeper-GPU provides an acceleration of 2.9x to sequence alignment and up to 1.4x speedup to the end-to-end execution time of a comprehensive read mapper (mrFAST).


# Citations

-  [**GateKeeper-GPU: Fast and Accurate Pre-Alignment Filtering in Short Read Mapping**](https://ieeexplore.ieee.org/document/10436437). Zülal Bingöl, Mohammed Alser, Onur Mutlu, Ozcan Ozturk, Can Alkan.  *IEEE Transactions on Computers*, epub Feb 14, 2024. [**arXiv preprint**](https://arxiv.org/abs/2103.14978) / [**Extended Abstract: HiCOMB 2021**](https://ieeexplore.ieee.org/abstract/document/9460690)
