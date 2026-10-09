# Generated matrix inventory

All rates are aggregate commands/s. Run counts and minimum hours include every selected version and repetition, plus warm-up, idle and the two-second post-drop observation; they exclude setup and drain.

| Stage | Build mode | Runs | Measured seconds | Connections | Total in flight | Pipeline batch | Repeats | Minimum hours |
| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |
| small-capacity-pilot | scored | 6 | 30 | 1,16 | 128 | 1 | 1 | 0.06 |
| screening | scored | 225 | 120 | 1,2,4,8,16 | 16,64,128 | 1 | 3 | 8.88 |
| main | scored | 135 | 300 | 1,16,100 | 16,64,100,128 | 1 | 3 | 13.57 |
| knee-validation | scored | 135 | 300 | 2,4,8 | 16,64,128 | 1 | 3 | 13.57 |
| diagnostic | diagnostic | 180 | 180 | 1 | 1,4,16,64 | 1 | 3 | 12.50 |
| pipeline-screening | scored | 144 | 180 | 1,4,16 | 64 | 1,4,16 | 3 | 8.88 |
| synchronized-stress | scored | 27 | 120 | 1,16,100 | 64,100 | 1 | 3 | 1.22 |
| jemalloc-screening | jemalloc | 135 | 120 | 1,16,100 | 16,64,100,128 | 1 | 3 | 6.08 |
| confirmation | scored | 27 | 900 | 1,100 | 16,64,100 | 1 | 3 | 7.44 |
