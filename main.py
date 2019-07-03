import argparse
import collections
import datetime
import hashlib
import os
import platform
import random
import shutil
import signal
import subprocess
import sys
import time
import timeit
import unicodedata
from collections import OrderedDict
from subprocess import CalledProcessError
from pymongo import MongoClient
import pymongo
import operator

import nltk
from colorama import Back, Fore, Style, init

from combinators import combinator, combinator_registrar
from permutators import permutator, permutator_registrar

from mongo import db_lists, db_pws_wn, db_pws_lists, clear_mongo, store_tested_pass_lists, store_tested_pass_wn, init_word_list_object, append_lemma_to_wl, db_wn, store_synset_with_relatives, update_synset_with_stats, store_permutations_for_lemma, new_permutation_for_lemma, db_wn_lemma_permutations
from helper import log_ok, log_err, log_status, remove_control_characters, get_curr_time, get_curr_time_str, get_shell_width, clear_terminal, get_txt_files_from_dir, format_number

parser = argparse.ArgumentParser(
    description="Password hash anaylsis using WordNet and the HaveIBeenPwned database.")
parser.add_argument("-p", "--pass-database", type=str,
                    help="Path to the HIBP password database.", dest="pass_db_path")
parser.add_argument("-d", "--depth", type=int,
                    help="Depth in the DAG", dest="dag_depth")
parser.add_argument("-g", "--graph", action="store_true",
                    help="Display a directed graph for WordNet.", dest="draw_dag")
parser.add_argument("-s", "--root-syn-name", type=str,
                    help="Name of the word specified to be the root synset.", dest="root_syn_name")
parser.add_argument("-c", "--classification", action="store_true",
                    help="Subsume the hits for each class of the search hierarchy.", dest="subsume_for_classes")
parser.add_argument("--result-file", type=str,
                    help="Name of the result file.", dest="result_file_name")
parser.add_argument("--summary-file", type=str,
                    help="Name of the summary file.", dest="summary_file_name")
parser.add_argument("-l", "--from-lists", type=str,
                    help="Path to the folder containing self-created password lists.", dest="from_lists")
parser.add_argument("-z", "--download-wordnet", action="store_true",
                    help="Download WordNet.", dest="dl_wordnet")
parser.add_argument("-t", "--lookup-utility", action="store_true",
                    help="If set, use sgrep instead of the look utility.", dest="lookup_utility")
parser.add_argument("-v", "--verbose", action="store_true",
                    help="Verbose output.", dest="verbose")
parser.add_argument("-e", "--extensive", action="store_true",
                    help="Print all tested password to a separate result file. Use --result-file option to set custom file name..", dest="extensive")
parser.add_argument("--skip-warning", action="store_true",
                    help="Skip the warning when using the -e (--extensive) flag.", dest="skip_warning")
parser.add_argument("--test", action="store_true",
                    help="Test", dest="test")
parser.add_argument("--purge-db", action="store_true",
                    help="Purge Database before writing", dest="purge_db")
parser.add_argument("--classify-lists", type=str,
                    help="Classify lists. -l param is required!", dest="classify_lists")
parser.add_argument("--classify-wn", type=str,
                    help="Classify wordnet synsets.", dest="classify_wn")
parser.add_argument("--top", type=int,
                    help="Limit output for --classify-x queries.", dest="top")
parser.add_argument("--plot", type=str, help="Plot graph.", dest="plot")

# parser.add_argument("-z", "--is-debug", action="store_true",
#                     help="Debug mode.", dest="is_debug")
args = parser.parse_args()

started = ""
permutation_handler = None
hits_for_lemmas = OrderedDict()
hits_for_list_lemmas = OrderedDict()

# For tracking progress
total_processed = 0
total_hits_sum = 0
total_found = 0
total_not_found = 0
pwned_pw_amount = 551509767
counter = 0
total_base_lemmas = 0  # track the total number of base lemmas
lemmas_to_process = 0
glob_started_time = None
synset_cnt = 0

ILL_TAG = get_curr_time_str()


def sigint_handler(sig, frame):
    """
    Register the handler for the SIGINT signal.

    This is absolutely necessary to exit the program with Ctrl+C because a user easily misconfigure the
    programe (i.e. -d > 4) for it to result in a combinatorial explosion because of its recursion.
    """
    print()
    print("Caught Ctrl+C, shutting down...")
    cleanup()
    sys.exit(0)


def cleanup():
    """
    Some cleanup work like closing the file handler.
    """
    outfile_summary.close()
    outfile_passwords.close()


def _init_file_handles(started_time, of_summary=None):
    # Open the file handler for a file with the starting time
    global outfile_summary, outfile_passwords

    if of_summary:
        # of_summary option is used by the --classify-only handler so we can create only the summary file
        if args.summary_file_name is not None:
            outfile_summary_name = args.summary_file_name
        else:
            outfile_summary_name = "{0}_summary.txt".format(started_time)

        outfile_summary = open(outfile_summary_name, "w+")
    else:
        if args.summary_file_name is not None:
            outfile_summary_name = args.summary_file_name
        elif args.root_syn_name:
            outfile_summary_name = "{0}_{1}_summary.txt".format(
                started_time, args.root_syn_name)
        else:
            outfile_summary_name = "{0}_summary.txt".format(
                started_time)

        if args.result_file_name is not None:
            outfile_passwords_name = args.result_file_name
        elif args.root_syn_name:
            outfile_passwords_name = "{0}_{1}_passwords.txt".format(
                started_time, args.root_syn_name)
        else:
            outfile_passwords_name = "{0}_passwords.txt".format(
                started_time)

        outfile_summary = open(outfile_summary_name, "w+")
        outfile_passwords = open(outfile_passwords_name, "w+")


def inc_total_processed():
    """
    Increment the global variable to track the overall progress of processed lemmas.
    """
    global total_processed
    total_processed += 1


def inc_total_found():
    """
    Increment the global variable to track the passwords which could be found.
    """
    global total_found
    total_found += 1


def inc_total_not_found():
    """
    Increment the global variable to track the the passwords which could not be found.
    """
    global total_not_found
    total_not_found += 1


def lookup_pass(hash):
    """
    Wrapper for _lookup_in_hash_file. Returns the occurrences of the
    searched hash/password in the HIBP password file.
    """
    occurrences = _lookup_in_hash_file(hash)
    if occurrences is None:
        return 0
    else:
        return int(occurrences.split(":")[1])


def _lookup_in_hash_file(hash):
    """
    Implements actual file access.
    """
    try:
        if args.lookup_utility:
            result = subprocess.check_output(
                ["sgrep", "-i", "-b", hash, args.pass_db_path])
        else:
            result = subprocess.check_output(
                ["look", "-f", hash, args.pass_db_path])
    except CalledProcessError as e:
        return None
    return result.decode("utf-8").strip("\n").strip("\r")


def hash_sha1(s):
    """
    Hash the password.
    """
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def recurse_nouns_from_root(root_syn, start_depth, rel_depth=1):
    """
    Iterates over each hyponym synset until the desired depth in the DAG is reached.

    For each level of hyponyms in the DAG, this function will unpack each lemma of each
    synset of said depth level, which can be confusing when looking at results.txt.

    Each indented set of lemmas is the sum of all unpacked lemmas of each synset of the current graph level.
    """

    # If the current depth in the DAG is reached, do not continue to iterate this path.
    # Example:  rel_depth = 3, curr = 9, start = 7
    #           9 - 5 = 4,
    if (root_syn.min_depth() - start_depth) >= rel_depth:
        return 0, 0, 0
    global glob_started_time
    global total_base_lemmas
    curr_time = get_curr_time()
    time_diff = curr_time - glob_started_time

    clear_terminal()
    global synset_cnt
    synset_cnt += 1
    log_status("Processed Lemmas: {0}\nProcessed Synsets: {5} \nTested Passwords: {1}\nCurrent Lemma: {2}\nElapsed Time: {3}/{4:.2f} (s/m)".format(
        total_base_lemmas,
        total_processed,
        root_syn,
        time_diff.seconds,
        time_diff.seconds / 60,
        synset_cnt
    ))
    curr_root_syn = root_syn
    hits_below = 0
    total_hits_for_current_synset = 0
    not_found_for_current_synset = 0
    found_for_current_synset = 0
    for hypo in curr_root_syn.hyponyms():
        total_hits = 0
        not_found = 0
        found = 0
        for lemma in hypo.lemma_names():
            total_base_lemmas += 1
            # Apply a set of permutations to each lemma
            lemma_hits, not_found_cnt, found_cnt = permutations_for_lemma(
                lemma, hypo.min_depth(), hypo.name())
            total_hits += lemma_hits
            total_hits_for_current_synset += lemma_hits
            not_found += not_found_cnt
            found += found_cnt
            not_found_for_current_synset += not_found_cnt
            found_for_current_synset += found_cnt
        # Create this synset in the database and save its relatives (hypernym and hyponyms)
        store_synset_with_relatives(hypo, curr_root_syn.name())
        # Execute the function again with the new root synset being each hyponym we just found.
        hits_below, not_found_below, found_below = recurse_nouns_from_root(
            root_syn=hypo, start_depth=start_depth, rel_depth=rel_depth)

        # Add the sum of all hits below the current synset to the hits list of the current synset so
        # below hits are automatically included (not included in the terminal output, we separate both these
        # numbers into total_hits and hits_below so we can distinguis how many hits we found below and how
        # many were produced by the current synset).
        # Works because of... recursion
        total_hits_for_current_synset += hits_below
        not_found_for_current_synset += not_found_below
        found_for_current_synset += found_below
        # Update the synset with these stats
        update_synset_with_stats(
            hypo, hits_below, not_found_below, found_below, total_hits, found_cnt, not_found_cnt)
        if args.subsume_for_classes:
            append_with_hits(hypo, total_hits, hits_below,
                             not_found, not_found_below, found, found_below)
        total_base_lemmas += 1

    return total_hits_for_current_synset, not_found_for_current_synset, found_for_current_synset


def permutations_for_lemma(lemma, depth, source):
    total_hits = 0
    not_found_cnt = 0
    found_cnt = 0
    all_permutations = []
    for combination_handler in combinator.all:
        # Generate all permutations
        permutations = combination_handler(lemma, permutator.all)
        if args.verbose:
            log_status("Permutations for [%s]: %d" %
                       (lemma, len(permutations)))
        if permutations == None:
            continue
        # Combinators always return a list of permutations
        if type(permutations) == list:
            for p in permutations:
                if args.verbose:
                    log_status("Looking up [%s]" % p)
                trans_hits = lookup(p, depth, source, lemma)
                if args.root_syn_name:
                    # Store each permutations under this lemma object in the database
                    all_permutations.append(
                        new_permutation_for_lemma(p, trans_hits))
                if args.from_lists:
                    store_tested_pass_lists(p, trans_hits, source, lemma)
                total_hits += trans_hits
                if trans_hits == 0:
                    not_found_cnt += 1
                else:
                    found_cnt += 1
        else:
            if args.verbose:
                log_status("Looking up [%s]" % permutations)
            trans_hits = lookup(permutations, depth, source, lemma)
            if args.root_syn_name:
                all_permutations.append(
                    new_permutation_for_lemma(permutation, trans_hits))
            if args.from_lists:
                store_tested_pass_lists(permutation, trans_hits, source, lemma)

            if trans_hits == 0:
                not_found_cnt += 1
            else:
                found_cnt += 1
            total_hits += trans_hits

    if args.root_syn_name:
        permutations_for_lemma = {
            "word_base": lemma,
            "permutations": all_permutations,
            "total_permutations": len(all_permutations),
            "total_hits": total_hits,
            "synset": source
        }
        store_permutations_for_lemma(permutations_for_lemma)

    return total_hits, not_found_cnt, found_cnt


def lookup(permutation, depth, source, word_base):
    """
    Hashes the (translated) lemma and looks it up in  the HIBP password file.
    """
    # Hash and lookup translated lemma
    hashed_lemma = hash_sha1(permutation)
    occurrences = lookup_pass(hashed_lemma)
    # Increment "total" counter
    inc_total_processed()
    # Track found/not found
    if occurrences == 0:
        inc_total_not_found()
    else:
        inc_total_found()

    # Return occurrences in order to be able to subsume them for each class.
    global total_hits_sum
    total_hits_sum += occurrences
    return occurrences


def append_with_hits(lemma, total_hits, below_hits, not_found, not_found_below, found, found_below):
    global hits_for_lemmas
    res_set = [lemma, total_hits, below_hits,
               not_found, not_found_below, found, found_below]
    if lemma.name() in hits_for_lemmas:
        hits_for_lemmas[lemma.name()][1] += total_hits
    else:
        hits_for_lemmas[lemma.name()] = res_set


def _write_result_to_passwords_file(lemma_name, lemma_depth, occurrences):
    """
    Writes a properly indented result to the result file.
    """
    _write_to_passwords_file("%s%s %d" % (
        lemma_depth * "  ", lemma_name, occurrences))


def _write_summary_to_result_file(opts):
    """
    Writes the bottom lines containing the summary to the result file.
    """
    log_ok("Writing summary to result file...")
    # If we set the -c flag, instead of logging the single passwords that were searched,
    # we print only their respective classes to the result file
    if args.subsume_for_classes:
        _write_to_summary_file("")
        _write_to_summary_file(40 * "=")
        _write_to_summary_file("")
        _write_to_summary_file("    *** Synset Distribution ***")
        _write_to_summary_file("")

        # Reverse the list to restore the "natural" tree order.
        # Due to the nature of a recursion, thing, physical entity and abstraction get added before entity, however when we print this dict,
        # we want entity to be before its childs
        global hits_for_lemmas
        reversed_dict = collections.OrderedDict(
            reversed(list(hits_for_lemmas.items())))

        # The hits_for_lemmas dictionary contains all synset names (name.pos.nn) and their sum of hits
        for k, v in reversed_dict.items():
            synset_id = v[0].name()
            this_hits = v[1]
            below_hits = v[2]
            total_hits = v[1] + v[2]
            this_not_found = v[3]
            below_not_found = v[4]
            total_not_found_loc = v[3] + v[4]
            this_found = v[5]
            below_found = v[6]
            total_found_loc = v[5] + v[6]
            pct_total_of_total = (total_found_loc / total_found) * 100
            pct_this_of_total = (this_found / total_found) * 100
            _write_to_summary_file("{0}{1}  pct_total={2:.2f}|pct_this={12:.2f}|total_hits={3}|this_hits={4}|below_hits={5}|total_found={6}|this_found={7}|below_found={8}|total_not_found={9}|this_not_found={10}|below_not_found={11}".format(
                (v[0].min_depth() - opts["start_depth"]) *
                "  ",  # indendation
                synset_id,  # synset id
                pct_total_of_total,
                total_hits,  # hits of each synset
                this_hits,
                below_hits,
                total_found_loc,
                this_found,
                below_found,
                total_not_found_loc,
                this_not_found,
                below_not_found,
                pct_this_of_total))

        _write_to_summary_file("")
        _write_to_summary_file(40 * "=")
        _write_to_summary_file("")
        _write_to_summary_file("    *** Searched Lemma ***")
        _write_to_summary_file("")
        _write_to_summary_file("Identifier: %s" % opts["root_syn"].name())
        _write_to_summary_file("Synonyms: %s" %
                               opts["root_syn"].lemma_names())
        _write_to_summary_file("Definition: %s" %
                               opts["root_syn"].definition())
        _write_to_summary_file("Examples: %s" %
                               opts["root_syn"].examples())
        _write_to_summary_file("")
        _write_to_summary_file("    *** Stats ***")
        _write_to_summary_file("")
        _write_to_summary_file(
            "Total Passwords Searched: {0} ({1:.2f}%)".format(total_processed,
                                                              (total_processed / total_processed * 100)))
        _write_to_summary_file(
            "Total Passwords (Success): {0} ({1:.2f}%)".format(total_found,
                                                               (total_found / total_processed * 100)))
        _write_to_summary_file(
            "Total Passwords (Failure): {0} ({1:.2f}%)".format(total_not_found,
                                                               (total_not_found / total_processed * 100)))
        _write_to_summary_file(
            "Total hits for password searches: {0} ({1:.2f} hits per password)".format(
                total_hits_sum, total_hits_sum / total_processed))
        _write_to_summary_file("")
        _write_to_summary_file("Pct Found Passwords (Total): {0:.5f}%".format(
                               (total_hits_sum / pwned_pw_amount * 100)))
        _write_to_summary_file("Pct Not Found Passwords (Total): {0:.5f}%".format(
                               ((1 - (total_hits_sum / pwned_pw_amount)) * 100)))
        _write_to_summary_file("")
        _write_to_summary_file("Base Lemmas (Total): {0} ({1:.2f} permutations per base lemma)".format(
            total_base_lemmas, total_processed / total_base_lemmas))
        _write_to_summary_file("")
        _write_to_summary_file("")
        started_time = opts["started_time"]
        finished_time = get_curr_time()
        time_delta = finished_time - started_time
        _write_to_summary_file(
            "Average Time per Base Lemma: {0:.3f} s".format(time_delta.seconds / total_base_lemmas))
        _write_to_summary_file("Starting Time: %s" % started_time)
        _write_to_summary_file("Finishing Time: %s" % finished_time)
        log_ok("Writing summary to %s" % outfile_summary.name)
        log_ok("Writing tested passwords to %s" % outfile_passwords.name)


def _write_to_summary_file(s):
    """
    Writes generic data to the result file.
    """
    outfile_summary.write("%s\n" % s)


def _write_to_passwords_file(s):
    """
    Writes generic data to the result file.
    """
    outfile_passwords.write("%s\n" % s)


def prompt_synset_choice(root_synsets):
    print("  Multiple synset were found. Please choose: ")
    for elem in range(len(root_synsets)):
        print("    [{0}] Name: {1}, Synonyms: {2}".format(
            elem,
            root_synsets[elem].name(),
            root_synsets[elem].lemma_names()))
    print()
    choice = input("Your choice [0-%d]: " % ((len(root_synsets)-1)))
    print()
    try:
        int_choice = int(choice)
    except ValueError:
        print("Invalid choice: %s" % choice)
        sys.exit(0)
    if int_choice < 0 or int_choice > len(root_synsets) - 1:
        print("Invalid choice: %s" % choice)
        sys.exit(0)
    # Make the choice the new root synset from we will start our recursion.
    return root_synsets[int_choice]


def _download_wordnet():
    """
    Download the NLTK wordnet corpus.
    """
    nltk.download("wordnet")
    sys.exit(0)


def option_draw_graph():
    """
    Draw the graph.
    """
    from wn_graph import draw_graph
    draw_graph(args.root_syn_name, args.dag_depth)


def option_lookup_passwords():
    """
    Lookup the passwords in the pwned passwords list.
    """
    init()
    signal.signal(signal.SIGINT, sigint_handler)
    clear_terminal()
    if args.purge_db:
        clear_mongo()
        log_ok("Database was cleared!")
    print()
    started_time = get_curr_time()
    global glob_started_time
    glob_started_time = started_time

    root_synsets = wn.synsets(args.root_syn_name, "n")
    if len(root_synsets) == 0:
        print("  No synset found for: %s" % args.root_syn_name)
        sys.exit(0)

    # If multiple synsets were found, prompt the user to choose which one to use.
    if len(root_synsets) > 1:
        choice_root_syn = prompt_synset_choice(root_synsets)
    else:
        choice_root_syn = root_synsets[0]

    # Initiate the file handles for the result and summary file
    _init_file_handles(get_curr_time_str())
    global total_base_lemmas
    log_ok("Processing user-specified WordNet root level...")
    first_level_hits = 0
    first_level_not_found = 0
    first_level_found = 0
    for root_lemma in choice_root_syn.lemma_names():
        total_base_lemmas += 1
        hits, not_found, found = permutations_for_lemma(
            root_lemma, choice_root_syn.min_depth(), choice_root_syn.name())
        first_level_hits += hits
        first_level_not_found += not_found
        first_level_found += found

    log_ok("Processing WordNet subtrees...")
    # Store this synset including all of its hyponyms.
    # By emitting the parent parameter, we declare this synset the root
    # We will only declare entity.n.01 as root since it is the actual root object of the wordnet tree
    if choice_root_syn.name() == "entity.n.01":
        store_synset_with_relatives(choice_root_syn, parent="root")
    else:
        # If we run the script starting from somewhere within the wordnet,
        # we still need to find its parent (hypernym). Therefore, we call hypernyms()
        # on the synset and check if one of the returned hypernyms already exists
        # in our database. If it does, we connect it by setting this synset's parent
        # to the found parent. Note that the first occurence of the hypernym
        # will be specified its parent (even if there might be more valid hypernyms existing in
        # the database)
        root_hypernyms = choice_root_syn.hypernyms()
        root_hypernym = None
        for hypernym in root_hypernyms:
            if db_wn.count_documents({"id": hypernym.name()}) == 0:
                continue
            else:
                root_hypernym = hypernym
        if root_hypernym is None:
            log_err(
                "Could not find a single hypernym of [%s] in the database to link to" % choice_root_syn.name())
            log_err("\t Hypernyms for [%s]: %s" % (
                choice_root_syn.name(), choice_root_syn.hypernyms()))
            sys.exit(0)
        _write_to_passwords_file("found root_hypernym for %s is %s" % (
            choice_root_syn.name(), root_hypernym))
        store_synset_with_relatives(
            choice_root_syn, parent=root_hypernym.name())
    hits_below, not_found_below, found_below = recurse_nouns_from_root(
        root_syn=choice_root_syn, start_depth=choice_root_syn.min_depth(), rel_depth=args.dag_depth)

    # Update this root synset with its respective stats
    update_synset_with_stats(choice_root_syn, hits_below, not_found_below,
                             found_below, hits, found, not_found)
    # we processed the subtrees, since we are going to reverse
    # the entire OrderedDict. Because of the recursion, the synsets are going to be added from hierarchical
    # bottom to top to the OrderedDict. If we just reverse it, we have the top to bottom order back.
    if args.subsume_for_classes:
        append_with_hits(choice_root_syn, first_level_hits,
                         hits_below, first_level_not_found, not_found_below, first_level_found, found_below)

    # Writing results to result file
    # Using a options dictionary to pass option information to the function
    opts = {}
    opts["root_syn"] = choice_root_syn
    opts["started_time"] = started_time

    opts["hits_below_root"] = hits_below
    opts["start_depth"] = choice_root_syn.min_depth()
    _write_summary_to_result_file(opts)
    cleanup()

    # Append the dict with the root synset after


def option_permutate_from_lists():
    signal.signal(signal.SIGINT, sigint_handler)
    clear_terminal()
    if args.purge_db:
        clear_mongo()
        log_ok("Database was cleared!")
    # Initialize the file handles to write to
    _init_file_handles(get_curr_time_str())
    # Check if the specified directory is valid
    log_ok("Checking prerequisites...")
    if not args.from_lists:
        log_err("Please enter a path to a directory containing password base lists.")
        return
    # Check if directory exists and is a directory
    if not os.path.isdir(args.from_lists):
        log_err("Not a directory")
        return

    # Check if directory contains at least 1 file
    if len(os.listdir(args.from_lists)) == 0:
        log_err("Directory is empty.")
        return

    dir_txt_content = get_txt_files_from_dir(args.from_lists)
    if len(dir_txt_content) > 0:
        log_status("Found %d text files in %s" %
                   (len(dir_txt_content), args.from_lists))
    else:
        log_err("Could not find any textfiles")

    started_time = get_curr_time()

    # Create options dict
    opts = {}
    opts["started_time"] = started_time
    opts["list_dir"] = args.from_lists
    # Iterate over each list
    global total_base_lemmas
    # for progress tracking
    global lemmas_to_process
    for txt_file in dir_txt_content:
        try:
            result = subprocess.check_output(
                ["wc", "-l", "{0}/{1}".format(args.from_lists, txt_file)])
        except CalledProcessError as e:
            log_err(
                "Could not count lines for destination directory! % s" % e)
            return None
        result = result.decode(
            "utf-8").strip("\n").strip("\r").lstrip().split(" ")[0]
        lemmas_to_process += int(result)
    log_status("Total lemmas to process: %d" % lemmas_to_process)
    log_status("Starting: %s" % started_time)
    # Track finished lists
    finished_lists = 0
    # Iterate over each list in the specified directory
    for pass_list in dir_txt_content:
        # Check if a ill document for this list name already exists
        if db_lists.count_documents({"filename": pass_list}) > 0:
            log_status(
                "%s already exists in database, will append results to this document" % pass_list)
        else:
            # Create new document "frame"
            init_word_list_object(pass_list)

        if args.verbose:
            log_status("Processing: %s" % pass_list)
        try:
            pass_file = open("%s/%s" % (args.from_lists, pass_list))
            curr_pass_list = pass_file.readlines()
            pass_file.close()
        except Exception as e:
            log_err("Failed to open file '%s'" % pass_list)
            # Continue with next file instead of terminating the script
            continue
        if args.verbose:
            log_status("Read all entries for: %s" % pass_list)
        for password_base in curr_pass_list:
            if password_base[0] == "#" or password_base == "" or password_base == " " or password_base == "\n":
                if args.verbose:
                    log_status("[%s] is a non-lemma. Skipping!" %
                               password_base)
                continue
            else:
                password_base = remove_control_characters(password_base)
                if args.verbose:
                    log_status(
                        "Creating permutations for [%s]" % password_base)
                total_base_lemmas += 1
                total_hits, not_found_cnt, found_cnt = permutations_for_lemma(
                    password_base, 0, pass_list)
                append_list_lemma_to_list(
                    pass_list, password_base, total_hits, found_cnt, not_found_cnt)
                if args.verbose:
                    log_status("Finished Lemma [%s]" % password_base)

            curr_time = get_curr_time()
            time_diff = curr_time - started_time
            curr_lemma_time = time_diff.seconds / total_base_lemmas
            remaining_lemmas = lemmas_to_process - total_base_lemmas
            remaining_time_est = remaining_lemmas * curr_lemma_time
            clear_terminal()
            log_status("Current list: {0}\nProcessed Lemmas: {1}/{2}\nTested Passwords: {7}\nFinished Lists: {8}/{9}\nCurrent Lemma: {10}\nElapsed Time (seconds): {3:.2f}\nEstimated Remaining Time (m/h): {4:.2f}/{5:.2f}\nCurrent Average Time per Lemma (s): {6:.2f}\n".format(
                pass_list,
                total_base_lemmas,
                lemmas_to_process,
                time_diff.seconds,
                remaining_time_est / 60,
                remaining_time_est / 60 / 60,
                curr_lemma_time,
                total_processed,
                finished_lists,
                len(dir_txt_content),
                password_base))

            # Append the finished lemma to the ill collection
            append_lemma_to_wl(password_base, total_hits,
                               found_cnt, not_found_cnt, pass_list, tag=ILL_TAG)

        finished_lists += 1

    # if args.subsume_for_classes:
    #     create_classification_for_lists(dir_txt_content)
        # _write_lists_summary_to_result_file(opts)
    print()
    cleanup()


def create_classification_for_lists(word_lists=None):
    """
    Use the data stored in the database to create a classification (and summary).
    """
    # If no word list was passed, e.g. if this function was not called by another function, we need to get our list names from a directory
    if word_lists is None:
        # We use the -l parameter to pass a destination directory containing the lists
        word_lists = get_txt_files_from_dir(args.from_lists)
        if len(word_lists) == 0:
            log_err(
                "Could not find any text files in %s to create classifications from" % args.from_lists)
            sys.exit(0)
        # We only need to manually create the summary file if this function is not called from "permutate_from_lists".
        # When this function is called first, it already created the file handles, so we don't need to do that again
        _init_file_handles(ILL_TAG, of_summary=True)

    _write_to_summary_file("File created: %s" % ILL_TAG)
    _write_to_summary_file("")

    if args.top == None:
        # limit = 0 does not limit the query
        query_limit = 0
    else:
        query_limit = args.top

    if args.classify_lists == "all":
        # Iterate over each word list stored in the database
        for filename in word_lists:
            print(filename)
            doc = db_lists.find_one({"filename": filename})
            if doc is None:
                continue
            all_lemmas = doc["lemmas"]
            if len(all_lemmas) == 0:
                _write_to_summary_file("\t <no lemmas stored for this list>")
            # Get the total sum of hits of all hits for all permutations for this list
            total_occurrences_list = 0
            for lemma in all_lemmas:
                total_occurrences_list += lemma["occurrences"]
            all_lemmas = sorted(
                all_lemmas, key=lambda k: k["occurrences"], reverse=True)
            _write_to_summary_file("***%s (Total occurrences: %s)" %
                                   (filename, format_number(total_occurrences_list)))
            _write_to_summary_file("")
            # Iterate over each lemma of each list
            for lemma in all_lemmas:
                _write_to_summary_file("\t+ %s" % (lemma["name"]))
                _write_to_summary_file(
                    "\t|-Total Occurrences: %s" % format_number(lemma["occurrences"]))
                _write_to_summary_file(
                    "\t|-Generated Permutations: %s" % (format_number(lemma["found_cnt"] + lemma["not_found_cnt"])))
                _write_to_summary_file(
                    "\t|-Found Permutations: %s" % format_number(lemma["found_cnt"]))
                _write_to_summary_file(
                    "\t|-Not Found Permutations: %s" % format_number(lemma["not_found_cnt"]))
                _write_to_summary_file("\t|-Occurrences in this list: {:.2f}%".format(
                    lemma["occurrences"] / total_occurrences_list * 100))
                _write_to_summary_file("")
            _write_to_summary_file("")
        log_ok("Classification written to the summary file.")
    elif args.classify_lists == "sort_password_desc":
        for password in db_pws_lists.find().sort("occurrences", pymongo.DESCENDING).limit(query_limit):
            print("{}\t{}".format(password["occurrences"], password["name"]))
    elif args.classify_lists == "sort_list_desc":
        for l in db_lists.find().sort("total_hits", pymongo.DESCENDING).limit(query_limit):
            print("{}\t{}".format(l["total_hits"], l["filename"]))
    else:
        log_err("Unrecognized classification option [%s]" % args.classify_wn)


def create_complete_classification_for_wn():
    # Check if we have synsets to iterate over
    if db_wn.count_documents({}) == 0:
        log_err("No synsets found in database. Nothing to process.")
        sys.exit(0)

    _init_file_handles(ILL_TAG, of_summary=True)
    # Get the root object
    tree_root = db_wn.find_one({"parent": "root"})

    _write_to_summary_file("File created: %s" % ILL_TAG)
    _write_to_summary_file("")

    if args.top == None:
        # limit = 0 does not limit the query
        query_limit = 0
    else:
        query_limit = args.top

    if args.classify_wn == "sort_synset_desc":
        # Sort all stored synsets based on their total_hits field (so their hits as well as their hyponym hits) in descending order
        for synset in db_wn.find().sort("total_hits", pymongo.DESCENDING):
            # o = wn.synset(synset["id"])
            print("{}\t\t{}".format(synset["total_hits"], synset["id"]))
    elif args.classify_wn == "sort_lemma_desc":
        # Sort all lemmas (word bases) based on their hits in descending order

        for lemma in db_wn_lemma_permutations.find().sort("total_hits", pymongo.DESCENDING).limit(query_limit):
            print("{}\t\t{}".format(lemma["total_hits"], lemma["word_base"]))
    elif args.classify_wn == "sort_password_desc":
        for password in db_pws_wn.find().sort("occurrences", pymongo.DESCENDING).limit(query_limit):
            print("{}\t{}".format(
                password["occurrences"], password["name"], password["synset"]))
    else:
        log_err("Unrecognized classification option [%s]" % args.classify_wn)


def append_list_lemma_to_list(list_name, lemma, total_hits, found_count, not_found_count):
    global hits_for_list_lemmas
    content = [total_hits, found_count, not_found_count]
    if not list_name in hits_for_list_lemmas:
        hits_for_list_lemmas[list_name] = {}

    hits_for_list_lemmas[list_name][lemma] = content
    # Update the total stats for the file
    # Add the total hits to the file total hits
    if not "_total_hits" in hits_for_list_lemmas[list_name]:
        hits_for_list_lemmas[list_name]["_total_hits"] = total_hits
    else:
        hits_for_list_lemmas[list_name]["_total_hits"] += total_hits

    if not "_found_count" in hits_for_list_lemmas[list_name]:
        hits_for_list_lemmas[list_name]["_found_count"] = found_count
    else:
        hits_for_list_lemmas[list_name]["_found_count"] += found_count

    if not "_not_found_count" in hits_for_list_lemmas[list_name]:
        hits_for_list_lemmas[list_name]["_not_found_count"] = not_found_count
    else:
        hits_for_list_lemmas[list_name]["_not_found_count"] += not_found_count


def plot_data():
    import plots
    # evaluate the arguments
    opts = {}
    if not args.top:
        opts["top"] = 10
    else:
        opts["top"] = args.top

    if not args.from_lists:
        opts["ref_list"] = None
    else:
        opts["ref_list"] = args.from_lists

    if args.plot == "wn_passwords_bar":
        plots.wn_top_passwords_bar(opts)
    elif args.plot == "lists_passwords_bar":
        plots.lists_top_passwords_bar(opts)
    elif args.plot == "wn_passwords_line":
        plots.wn_top_passwords_line(opts)
    elif args.plot == "lists_passwords_line":
        plots.lists_top_passwords_line(opts)
    elif args.plot == "test_plot":
        plots.test_plot(opts)
    elif args.plot == "top_1k_wn":
        plots.wn_top_1k(opts)
    elif args.plot == "top_1k_wn_bar":
        plots.wn_top_1k_bar(opts)
    elif args.plot == "top_1k_wn_bar_test":
        plots.wn_top_1k_bar_test(opts)
    elif args.plot == "wn_line_noteable_pws":
        plots.wn_line_plot_noteable_pws(opts)
    elif args.plot == "wn_line_list_categories":
        plots.wn_line_plot_categories(opts)
    else:
        log_err("Unrecognized plotting option option [%s]" % args.plot)


if __name__ == "__main__":
    if args.dl_wordnet:
        _download_wordnet()

    if args.extensive:
        log_ok("WARNING: You set the -e (--extensive) flag writes EVERY tested password to a seperate file. " +
               "Note that this is going to slow down the script a lot, since file I/O is slow. This flag can increase " +
               "the overall runtime of the script by a factor of 20-25 (and even more).")
        if args.skip_warning:
            pass
        else:
            log_status("ENTER to continue.")
            temp = input()

    if args.lookup_utility:
        log_status("NOTE: Make sure you have sgrep installed and added to PATH.")
        print()
        print()

    from nltk.corpus import wordnet as wn

    # WordNet graph
    if args.draw_dag:
        # Evaluate command line parameters
        if args.dag_depth is None or args.root_syn_name is None:
            log_err("Missing parameters.")
            parser.print_usage()
            sys.exit(0)
        option_draw_graph()
    # Lookup words from self-created lists
    elif args.classify_lists:
        # if args.from_lists is None:
        #     log_err("Missing parameters.")
        #     sys.exit(0)
        create_classification_for_lists()
    elif args.classify_wn:
        create_complete_classification_for_wn()
    elif args.from_lists and not args.plot:
        option_permutate_from_lists()
    elif args.plot:
        plot_data()
    else:
        # Evaluate command line parameters
        if args.pass_db_path is None or args.dag_depth is None or args.root_syn_name is None:
            log_err("Missing parameters.")
            parser.print_usage()
            sys.exit(0)
        option_lookup_passwords()
