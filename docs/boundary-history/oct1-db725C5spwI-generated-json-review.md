# Oct 1 Generated JSON Review

Source VOD: `https://www.youtube.com/watch?v=db725C5spwI`

Generated project: `backtests/projects/oct1-db725C5spwI.generated.tvh.json`

Seed project: `backtests/projects/oct1.tvh.json`

## Summary

The unlisted VOD was reachable and downloaded at 480p for local analysis. The generated project uses the Oct 1 seed JSON, local boundary analyzer candidates, start.gg bracket metadata for Basement Brawl #8, and manual review of candidate frames.

The important correction is that Grand Finals is split into two matches:

- `Grand Final`
- `Grand Final Reset`

The seed also had late-round labels that did not match the bracket; those were corrected for Losers Semi-Final and Losers Final.

## Selected Matches

| ID | Round | Players | Start | End | Decision |
| --- | --- | --- | ---: | ---: | --- |
| 1 | Winners Round 1 | Pillaibro vs gandhi | `00:01:45.000` | `00:22:40.560` | Analyzer-selected stage select start and controller prompt end. |
| 2 | Winners Quarter-Final | Mr.DJ vs PWDDA \| Ariz | `00:23:15.560` | `00:24:23.560` | Analyzer did not find a clean stage select; kept seed rough start and accepted prompt end. |
| 3 | Losers Round 1 | Pillaibro vs Mr.DJ | `00:26:56.362` | `00:35:03.831` | Analyzer-selected stage select start and controller prompt end. |
| 4 | Winners Semi-Final | Spitfire2929 vs PWDDA \| Ariz | `00:37:46.831` | `01:03:08.290` | No clean stage select was visible; manually selected earliest safe pre-intro point before gameplay. |
| 5 | Losers Semi-Final | Ariz vs derpingtoast | `01:08:51.290` | `01:21:49.070` | Analyzer-selected stage select start; round corrected from bracket metadata. |
| 6 | Losers Final | andy vs derpingtoast | `01:24:02.070` | `01:35:11.929` | Analyzer did not find a clean stage select; kept seed rough start and accepted prompt/menu end. |
| 7 | Grand Final | andy vs Spitfire2929 | `01:37:15.929` | `01:52:16.929` | Manual start at character select because no clean stage select was visible; endpoint is the prompt/menu before reset. |
| 8 | Grand Final Reset | andy vs Spitfire2929 | `01:52:40.000` | `02:03:40.000` | Manual reset split. Start is character select after reset; end is the first high-confidence final prompt endpoint. |

## Notes

- The analyzer was strongest when the stage-select screen was actually present near the rough timestamp.
- Several Oct 1 matches appear to have no retained/visible clean stage-select frame in the searched window, so I used conservative manual starts at character select or the earliest pre-intro frame.
- The reset did not produce a confident stage-select hit; the first reliable reset-specific boundary was the character-select screen around `01:52:40`.
- Final endpoint was selected at `02:03:40`, a high-confidence post-match prompt frame.
