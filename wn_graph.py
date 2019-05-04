import networkx as nx
import matplotlib as mpl
import matplotlib.pyplot as plt
from networkx.drawing.nx_agraph import graphviz_layout
import random
import pydot
from nltk.corpus import wordnet as wn


# To install pygraphviz on windows use the pre-built python binaries/wheel:
# https://github.com/CristiFati/Prebuilt-Binaries/tree/master/Windows/PyGraphviz

def draw_graph(root_syn_name, max_depth):
    """
    Draw a directed acyclic graph representing the WordNet.

    root_syn_name: The synset the drawing will start from.
    max_depth: the level until which this function should draw the graph.
    """
    G = nx.DiGraph()
    root_synsets = wn.synsets(root_syn_name)
    if len(root_synsets) == 0:
        print("  No synset found for: %s" % root_syn_name)
        return

    # If multiple synsets were found, prompt the user to choose which one to use.
    if len(root_synsets) > 1:
        print("  Multiple synset were found. Please choose: ")
        for elem in range(len(root_synsets) - 1):
            print("    [%d] %s" % (elem, root_synsets[elem]))
        choice = input("Your choice [0-%d]: " % ((len(root_synsets)-2)))
        try:
            int_choice = int(choice)
        except ValueError:
            print("Invalid choice: %s" % choice)
            return
        if int_choice < 0 or int_choice > len(root_synsets) - 2:
            print("Invalid choice: %s" % choice)
    # Make the choice the new root synset from we will start our recursion.
    choice_root_syn = root_synsets[int_choice]

    # Create the first node in the digraph
    G.add_node(choice_root_syn.name())
    _recurse_nouns_from_root(G, choice_root_syn, max_depth)

    pos = hierarchy_pos(G, choice_root_syn.name())
    labels = nx.get_node_attributes(G, 'depth')
    # nx.draw(G, pos=pos, with_labels=True, labels=labels)
    nx.draw(G, pos=pos, with_labels=True)

    plt.title("WordNet")
    plt.show()


def _recurse_nouns_from_root(G, root_syn, max_depth=0):
    """
    Iterate over the entire WordNet starting from root_syn and running until the max_depth layer is reached.
    """
    # TODO: We can not work with the absolute depth anymore, since the user can now specify at which level to start!!!
    # If the current depth in the DAG is reached, do not continue to iterate this path.
    if root_syn.min_depth() == max_depth:
        return
    curr_root_syn = root_syn
    for hypo in curr_root_syn.hyponyms():
        # Add the new synset as a node to the digraph
        G.add_node(hypo.name())
        # Create the edge between this synset and its hypernym
        G.add_edge(root_syn.name(), hypo.name())
        # Execute the function again with the new root synset being each hyponym we just found.
        _recurse_nouns_from_root(G, root_syn=hypo, max_depth=max_depth)


def hierarchy_pos(G, root=None, width=1., vert_gap=0.2, vert_loc=0, xcenter=0.5):
    '''
    From Joel's answer at https://stackoverflow.com/a/29597209/2966723.  
    Licensed under Creative Commons Attribution-Share Alike 

    If the graph is a tree this will return the positions to plot this in a 
    hierarchical layout.

    G: the graph (must be a tree)

    root: the root node of current branch 
    - if the tree is directed and this is not given, 
      the root will be found and used
    - if the tree is directed and this is given, then 
      the positions will be just for the descendants of this node.
    - if the tree is undirected and not given, 
      then a random choice will be used.

    width: horizontal space allocated for this branch - avoids overlap with other branches

    vert_gap: gap between levels of hierarchy

    vert_loc: vertical location of root

    xcenter: horizontal location of root
    '''
    if not nx.is_tree(G):
        raise TypeError(
            'cannot use hierarchy_pos on a graph that is not a tree')

    if root is None:
        if isinstance(G, nx.DiGraph):
            # allows back compatibility with nx version 1.11
            root = next(iter(nx.topological_sort(G)))
        else:
            root = random.choice(list(G.nodes))

    def _hierarchy_pos(G, root, width=1., vert_gap=0.2, vert_loc=0, xcenter=0.5, pos=None, parent=None):
        '''
        see hierarchy_pos docstring for most arguments

        pos: a dict saying where all nodes go if they have been assigned
        parent: parent of this branch. - only affects it if non-directed

        '''

        if pos is None:
            pos = {root: (xcenter, vert_loc)}
        else:
            pos[root] = (xcenter, vert_loc)
        children = list(G.neighbors(root))
        if not isinstance(G, nx.DiGraph) and parent is not None:
            children.remove(parent)
        if len(children) != 0:
            dx = width/len(children)
            nextx = xcenter - width/2 - dx/2
            for child in children:
                nextx += dx
                pos = _hierarchy_pos(G, child, width=dx, vert_gap=vert_gap,
                                     vert_loc=vert_loc-vert_gap, xcenter=nextx,
                                     pos=pos, parent=root)
        return pos

    return _hierarchy_pos(G, root, width, vert_gap, vert_loc, xcenter)
