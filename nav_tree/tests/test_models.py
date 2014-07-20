from django.test import TestCase

from ..models import Node
from .factories import NodeFactory


class NodeTest(TestCase):
    def test_fields(self):
        expected = (
            'id',
            'parent',
            'parent_id',
            'slug',
            'url',

            # MPTT fields
            'level',
            'lft',
            'rght',
            'tree_id',

            # Incoming foreign keys
            'children',  # FK from self. The other end of "parent".
        )
        fields = Node._meta.get_all_field_names()
        self.assertCountEqual(fields, expected)


class NodeValidateOnSave(TestCase):
    def test_create_root_with_slug(self):
        """Root must not have a slug"""
        root_node = NodeFactory.build(slug='slug', parent=None)

        with self.assertRaises(ValueError):
            root_node.save()

    def test_create_leaf_without_slug(self):
        """Leaf nodes must have a slug"""
        root_node = NodeFactory.create(slug='', parent=None)
        leaf = NodeFactory.build(slug='', parent=root_node)

        with self.assertRaises(ValueError):
            leaf.save()


class NodeSkipUpdateWithoutChange(TestCase):
    def setUp(self):
        self.root = NodeFactory.create(slug='', parent=None)

    def test_no_update_without_changes(self):
        """Saving unchanged Node shouldn't query parent to rebuild the url."""
        branch = NodeFactory.create(slug='branch', parent=self.root)
        branch = Node.objects.get(pk=branch.pk)
        # Prove that no attempt is made to update descendants.
        with self.assertNumQueries(1):
            # One query:
            # * Update the root.
            branch.save()

    def test_no_update_on_resave(self):
        """Resaving changed Node should only update descendants once."""
        branch = NodeFactory.create(slug='branch', parent=self.root)
        NodeFactory.create(slug='leaf', parent=branch)
        branch.slug = 'new_slug'
        branch.save()

        # Prove that no attempt is made to update descendants.
        with self.assertNumQueries(1):
            # One query:
            # * Update the root.
            branch.save()


class NodeCachesURLOnCreateTest(TestCase):
    def setUp(self):
        self.root = NodeFactory.create(slug='', parent=None)

    def test_create_root(self):
        """Root node should be at the root url."""
        self.assertEqual(self.root.url, '/')

    def test_create_leaf_on_root(self):
        """Children of the root should be at /<slug>/."""
        leaf = NodeFactory.create(slug='leaf', parent=self.root)

        self.assertEqual(leaf.url, '/leaf/')

    def test_create_child_of_child(self):
        """Children of children should be at /<parent-slug>/<slug>/."""
        branch = NodeFactory.create(slug='branch', parent=self.root)
        leaf = NodeFactory.create(slug='leaf', parent=branch)

        self.assertEqual(leaf.url, '/branch/leaf/')


class NodeCachesURLOnRenameTest(TestCase):
    def setUp(self):
        self.root = NodeFactory.create(slug='', parent=None)

    def test_rename_leaf(self):
        """Changing slug on a leaf should update the cached url."""
        leaf = NodeFactory.create(slug='foo', parent=self.root)

        leaf.slug = 'bar'
        leaf.save()

        self.assertEqual(leaf.url, '/bar/')

    def test_rename_branch(self):
        """Changing a branch slug should update the child url."""
        branch = NodeFactory.create(slug='foo', parent=self.root)
        leaf = NodeFactory.create(slug='leaf', parent=branch)

        branch.slug = 'bar'
        branch.save()

        leaf = Node.objects.get(pk=leaf.pk)
        self.assertEqual(leaf.url, '/bar/leaf/')

    def test_rename_trunk(self):
        """Changing a trunk slug should update the grandchild url."""
        trunk = NodeFactory.create(slug='foo', parent=self.root)
        branch = NodeFactory.create(slug='branch', parent=trunk)
        leaf = NodeFactory.create(slug='leaf', parent=branch)

        trunk.slug = 'bar'
        trunk.save()

        leaf = Node.objects.get(pk=leaf.pk)
        self.assertEqual(leaf.url, '/bar/branch/leaf/')


class NodeCachesURLOnMoveTest(TestCase):
    def setUp(self):
        self.root = NodeFactory.create(slug='', parent=None)

    def test_move_leaf(self):
        """Moving a leaf onto a new branch should update the cached url."""
        branch = NodeFactory.create(slug='foo', parent=self.root)
        leaf = NodeFactory.create(slug='leaf', parent=branch)

        new_branch = NodeFactory.create(slug='bar', parent=self.root)
        leaf.parent = new_branch
        leaf.save()

        self.assertEqual(leaf.url, '/bar/leaf/')

    def test_move_branch(self):
        """Moving a branch onto a new trunk should update the leaf urls."""
        trunk = NodeFactory.create(slug='foo', parent=self.root)
        branch = NodeFactory.create(slug='branch', parent=trunk)
        leaf = NodeFactory.create(slug='leaf', parent=branch)

        new_trunk = NodeFactory.create(slug='bar', parent=self.root)
        branch.parent = new_trunk
        branch.save()

        leaf = Node.objects.get(pk=leaf.pk)
        self.assertEqual(leaf.url, '/bar/branch/leaf/')


class NodeManagerBestMatchForPathTest(TestCase):
    """
    Test Node.objects.best_match_for_path works with perfect url matches.

    All of these tests assert use of only one query:
        * Get the best node based on url:
            SELECT
                (LENGTH(url)) AS "length",
                <other fields>
            FROM "nav_tree_node"
            WHERE
                "nav_tree_node"."url" IN (
                    '/',
                    '/url/',
                    '/url/split/',
                    '/url/split/into/',
                    '/url/split/into/bits/')
            ORDER BY "length" DESC
            LIMIT 1
    """
    def test_get_root(self):
        root = NodeFactory.create(slug='', parent=None)
        with self.assertNumQueries(1):
            node = Node.objects.best_match_for_path('/')

        self.assertEqual(node, root)

    def test_get_leaf(self):
        root = NodeFactory.create(slug='', parent=None)
        leaf = NodeFactory.create(slug='leaf', parent=root)

        with self.assertNumQueries(1):
            node = Node.objects.best_match_for_path('/leaf/')

        self.assertEqual(node, leaf)

    def test_get_leaf_on_branch(self):
        root = NodeFactory.create(slug='', parent=None)
        branch = NodeFactory.create(slug='branch', parent=root)
        leaf = NodeFactory.create(slug='leaf', parent=branch)

        with self.assertNumQueries(1):
            node = Node.objects.best_match_for_path('/branch/leaf/')

        self.assertEqual(node, leaf)

    def test_get_branch_with_leaf(self):
        root = NodeFactory.create(slug='', parent=None)
        branch = NodeFactory.create(slug='branch', parent=root)
        NodeFactory.create(slug='leaf', parent=branch)

        with self.assertNumQueries(1):
            node = Node.objects.best_match_for_path('/branch/')

        self.assertEqual(node, branch)


class NodeManagerBestMatchForBrokenPathTest(TestCase):
    """
    Test Node.objects.best_match_for_path works without a perfect url match.

    All of these tests assert use of only one query:
        * Get the best node based on url:
            SELECT
                (LENGTH(url)) AS "length",
                <other fields>
            FROM "nav_tree_node"
            WHERE
                "nav_tree_node"."url" IN (
                    '/',
                    '/url/',
                    '/url/split/',
                    '/url/split/into/',
                    '/url/split/into/bits/')
            ORDER BY "length" DESC
            LIMIT 1
    """
    def test_throw_error_without_match(self):
        with self.assertNumQueries(1):
            with self.assertRaises(Node.DoesNotExist):
                Node.objects.best_match_for_path('/')

    def test_fall_back_to_root(self):
        root = NodeFactory.create(slug='', parent=None)

        with self.assertNumQueries(1):
            node = Node.objects.best_match_for_path('/absent-branch/')

        self.assertEqual(node, root)

    def test_fall_back_to_branch(self):
        root = NodeFactory.create(slug='', parent=None)
        branch = NodeFactory.create(slug='branch', parent=root)

        with self.assertNumQueries(1):
            node = Node.objects.best_match_for_path('/branch/absent-leaf/')

        self.assertEqual(node, branch)
