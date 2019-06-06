## To Do

- [x] Permutations of each lemma
- [x] Plot the results
- [x] Subsume the hit results for each class. Subsume only the "vanilla" lemmas or also their translations?
- [x] Don't replace result files, but rather create a new file for each run (using the date, keyword, command etc.)
- [x] Cat term "bug"
- [x] Replace update_stats by simple spinner
- [x] Count passwords which have not been found
- [x] Check other password brute force tools like John the Ripper or Hashcat which password variations they use
- [x] Show classes of each lemma
- [x] Better form of result visualization in results.txt
- [x] Show synset synonyms in synset selection menu
- [x] Only allow nouns in synset selection menu
- [x] Problem with not_found, found does not add up to total_found
- [x] Combine all permutators with each other
- [x] Create flat password lists, classify them as well
- [X] Add lemma permutation/search statistics to WordNet mode
- [X] Add stats for each word file (for example brands.txt had 40% of all results)
- [X] Add percentages in summary file for each word in list mode
- [X] Show estimated time / progress
- [ ] Somehow manage to print classification on runtime and not only after the program has finished working
- [ ] Catch all errors so program doesn't terminate somewhere within execution
- [ ] Port OpenBSD/Darwin look tool to Linux
- [ ] Write paper about this

## Alternative to 'look'

`sgrep` seems to be an efficient and reliable alternative to the 64-bit 
look utility provided MacOS.

(Archive)[https://sourceforge.net/projects/sgrep/]

Compilation: `cd sgrep/ && make`
