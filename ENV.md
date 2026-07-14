To use the conda environment, do:

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate brainreg
export PATH="$HOME/software/elastix-5.3.1/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/software/elastix-5.3.1/lib:$LD_LIBRARY_PATH"
```
I'll fold those elastix exports into the pipeline config so you don't repeat them.

---
