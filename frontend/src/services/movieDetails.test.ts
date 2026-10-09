import { normalizeMovieDetails } from './movieDetails.ts';

test('normalizes actual TMDB field shapes including directors, roles, keywords and language', () => {
  const movie = normalizeMovieDetails({
    id: 123, title: '测试电影', original_title: 'Fixture', poster_path: '/abc.jpg',
    overview: ' 完整简介 ', runtime: 90, vote_average: 7, vote_count: 0,
    genres: [{ name: '剧情' }], production_countries: [{ name: 'France' }],
    keywords: { keywords: [{ name: '成长' }] }, spoken_languages: [{ english_name: 'French' }],
    credits: { crew: [{ name: '导演甲', job: 'Director' }, { name: '编剧', job: 'Writer' }], cast: [{ name: '演员甲', character: '角色甲' }] },
  }, 123);
  expect(movie).toMatchObject({ tmdb_id: 123, overview: '完整简介', director: '导演甲', cast_roles: [{ name: '演员甲', character: '角色甲' }],
    spoken_languages: ['French'], keywords: ['成长'], poster_url: 'https://image.tmdb.org/t/p/w500/abc.jpg' });
});

test('missing optional metadata stays unavailable instead of inventing ratings or cast', () => {
  expect(normalizeMovieDetails({ id: 1, title: '测试', vote_average: '8', credits: null, poster_path: 'https://other.example/image.jpg' }, 1))
    .toMatchObject({ vote_average: null, runtime: null, cast: [], poster_url: null, overview: '' });
});

test('rejects mismatched movie identities and invalid responses', () => {
  expect(() => normalizeMovieDetails({ id: 2 }, 1)).toThrow('身份');
  expect(() => normalizeMovieDetails(null, 1)).toThrow('身份');
});
