# Generated matrix inventory

All rates are aggregate commands/s; times exclude setup and drain.

| Stage | Build mode | Runs | Measured seconds | Connections | Total in flight | Pipeline batch | Repeats | Minimum hours |
| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |
| small-capacity-pilot | scored | 6 | 30 | 1,16 | 128 | 1 | 1 | 0.06 |
| screening | scored | 75 | 120 | 1,2,4,8,16 | 16,64,128 | 1 | 1 | 2.96 |
| main | scored | 135 | 300 | 1,16,100 | 16,64,100,128 | 1 | 3 | 13.57 |
| knee-validation | scored | 135 | 300 | 2,4,8 | 16,64,128 | 1 | 3 | 13.57 |
| diagnostic | diagnostic | 60 | 180 | 1 | 1,4,16,64 | 1 | 1 | 4.17 |
| pipeline-screening | scored | 48 | 180 | 1,4,16 | 64 | 1,4,16 | 1 | 2.96 |
| synchronized-stress | scored | 9 | 120 | 1,16,100 | 64,100 | 1 | 1 | 0.41 |
| jemalloc-screening | jemalloc | 45 | 120 | 1,16,100 | 16,64,100,128 | 1 | 1 | 2.02 |
| confirmation | scored | 27 | 900 | 1,100 | 16,64,100 | 1 | 3 | 7.44 |
